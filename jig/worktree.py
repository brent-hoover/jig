"""Git worktree management for agent isolation."""

import asyncio
import errno
import logging
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from jig.atomic import atomic_write_text
from jig.code_metrics import ChangeMetrics, compute_change_metrics
from jig.models import MergeStrategy
from jig.safe_path import validate_safe_path_segment

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CommitResult:
    """Outcome of :func:`commit_worktree`.

    ``sha`` is the new commit SHA, or ``None`` when there was nothing to commit.
    ``metrics`` is the deterministic code-quality signal for the committed
    change, or ``None`` when nothing was committed or the signal failed to
    compute (a signal failure must never block the commit).
    """

    sha: str | None
    metrics: ChangeMetrics | None


class LintError(Exception):
    """Raised when unfixable lint violations remain after auto-fix."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__(f"{len(errors)} unfixable lint errors")


class MergeConflictError(RuntimeError):
    """Raised by ``merge_ticket`` when the merge aborts with conflicts.

    Distinct from a generic ``RuntimeError`` so the orchestrator can
    surface a dedicated ``merge_conflict`` ticket status instead of
    silently treating the ticket as resolved.
    """

    def __init__(
        self,
        ticket_id: str,
        source_branch: str,
        conflicted_files: list[str] | None = None,
    ) -> None:
        self.ticket_id = ticket_id
        self.source_branch = source_branch
        self.conflicted_files: list[str] = conflicted_files or []
        super().__init__(
            f"Merge conflict for {source_branch} (branch preserved for manual merge)"
        )


async def _run_git(cwd: Path, *args: str) -> str:
    """Run a git command and return stdout."""
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {stderr.decode().strip()}")
    return stdout.decode().strip()


async def create_worktree(
    project_path: Path,
    ticket_id: str,
    base_branch: str,
) -> Path:
    """Create a git worktree for a ticket.

    Returns the path to the worktree directory. Validates
    ``ticket_id`` because the path AND the git branch name (``jig/<id>``)
    are derived from it — a malformed id could escape the worktrees
    root or produce a dangerous git ref. Defense in depth: callers
    construct ``Ticket`` objects whose ``id`` is already validated,
    but raw-string callers (CLI, reviewers, sim driver) reach this
    helper too.
    """
    validate_safe_path_segment(ticket_id, "ticket_id")
    worktree_path = project_path / ".jig" / "worktrees" / ticket_id
    branch_name = f"jig/{ticket_id}"

    # Ensure at least one commit exists (worktrees require a valid ref)
    try:
        await _run_git(project_path, "rev-parse", "HEAD")
    except RuntimeError:
        await _run_git(
            project_path,
            "commit",
            "--allow-empty",
            "-m",
            "chore: initialize repository",
        )

    # If the branch already exists (leftover from a previous run), delete it
    # before creating the worktree so `-b` doesn't fail.
    try:
        await _run_git(project_path, "rev-parse", "--verify", branch_name)
        # Branch exists — remove it
        await _run_git(project_path, "branch", "-D", branch_name)
    except RuntimeError:
        pass  # Branch doesn't exist — good

    await _run_git(
        project_path,
        "worktree",
        "add",
        "-b",
        branch_name,
        str(worktree_path),
        base_branch,
    )

    # Per-commit reviewer hook installation lives at the call site
    # (``Orchestrator._ensure_worktree``) so the orchestrator can surface
    # ``install_per_commit_hook_or_warn``'s warning string as a SystemEvent
    # on the ticket thread (SF-I1). Keeping create_worktree side-effect-free
    # for that responsibility makes worktree lifecycle easier to test.
    return worktree_path


def install_per_commit_hook_or_warn(worktree_path: Path, ticket_id: str) -> str | None:
    """Install the per-commit reviewer hook; return a warning string
    on failure or None on success. The caller is expected to surface
    the warning as a SystemEvent on the ticket thread (SF-I1)."""
    try:
        from jig.hooks.per_commit import install_per_commit_hook

        install_per_commit_hook(worktree_path)
    except Exception as exc:  # noqa: BLE001
        _logger.warning("per-commit hook install failed for %s: %r", ticket_id, exc)
        return (
            f"per-commit reviewer hook install failed: {exc}. The "
            "agent can still work, but mechanical reviewers will not "
            "fire on each commit for this ticket."
        )
    return None


_MISSING_PROJECT_STUB = (
    "# Project Notes (jig-managed)\n"
    "\n"
    "No project-specific notes have been set yet. "
    "Add them at `.jig/CLAUDE.md` in the project root to make them visible to agents.\n"
)


async def sync_project_claude_md(worktree_path: Path, project_path: Path) -> None:
    """Render ``<project>/.jig/CLAUDE.md`` into ``<worktree>/CLAUDE.md`` and
    mark it uncommittable so it never appears in ``git status`` or commits.

    When the project source is missing, writes ``_MISSING_PROJECT_STUB`` instead
    so the project's committed ``/CLAUDE.md`` (if any) is still suppressed —
    "jig wins" holds even for un-scaffolded projects.

    Uncommittable mechanism: ``git update-index --skip-worktree`` when the
    file is tracked, otherwise an entry in ``info/exclude`` (resolved via
    ``git rev-parse --git-path``). Note: ``info/exclude`` is SHARED between
    the main repo and any linked worktrees — git has no per-worktree exclude
    file. Writing ``CLAUDE.md`` from a linked worktree means the main
    checkout will also ignore it; an acceptable concession since operators
    rarely want to track a root ``CLAUDE.md``. Both git operations are
    best-effort — failures log a warning but do not raise, since the
    content placement is the primary guarantee.

    No-op when ``worktree_path`` equals ``project_path``. Some jig spawns
    (init/spec-generator/concierge) run with the real project root as their
    worktree — calling sync there would clobber the operator's checked-in
    root ``CLAUDE.md`` and the skip-worktree mark would hide the damage from
    ``git status``.

    Async to match the rest of ``worktree.py`` (which uses
    ``asyncio.create_subprocess_exec`` via ``_run_git``) — keeps git work
    off the event loop when many spawns overlap.
    """
    if worktree_path.resolve() == project_path.resolve():
        _logger.debug(
            "sync_project_claude_md: project-root spawn at %s — skipping to avoid "
            "clobbering operator's root CLAUDE.md",
            worktree_path,
        )
        return

    content = _read_project_source_safely(project_path / ".jig", "CLAUDE.md")

    dst = worktree_path / "CLAUDE.md"
    # Atomic write via tempfile + os.replace: avoids both the symlink-follow
    # problem on the write side (os.replace replaces the directory entry
    # itself rather than following a symlink to write the target) and the
    # TOCTOU race a naive unlink-then-write would open (where another local
    # process recreates dst as a symlink between unlink and write).
    atomic_write_text(dst, content)

    # Migrate any legacy unanchored "CLAUDE.md" exclude entry left by earlier
    # versions of this code, REGARDLESS of whether the file ends up tracked
    # (skip-worktree branch) or untracked (info/exclude branch). A tracked
    # root CLAUDE.md + stale legacy entry would otherwise keep nested
    # `docs/CLAUDE.md` etc. silently ignored.
    await _purge_local_exclude_patterns(worktree_path, ("CLAUDE.md",))

    if await _is_tracked(worktree_path, "CLAUDE.md"):
        await _mark_skip_worktree(worktree_path, "CLAUDE.md")
    else:
        # Anchored pattern (leading "/" in gitignore syntax = repo root only)
        # so nested `docs/CLAUDE.md` etc. remain visible to git.
        await _add_to_local_exclude(worktree_path, "/CLAUDE.md")


def _read_project_source_safely(parent: Path, name: str) -> str:
    """Read ``parent / name`` as text, refusing to follow symlinks on either
    the parent directory OR the final component. Returns the missing-project
    stub when the source is absent, a symlink, not a directory, or not a
    regular file.

    Uses ``os.open(parent, O_NOFOLLOW | O_DIRECTORY)`` for the parent and
    ``os.open(name, ..., dir_fd=parent_fd)`` (openat) for the child, so
    neither a symlinked ``.jig/`` directory nor a symlinked ``CLAUDE.md``
    can escape the project root. ``O_NONBLOCK`` avoids hanging on FIFOs,
    and ``os.fstat`` rejects non-regular files before any data is read.
    Single-fd open + fstat closes the TOCTOU window a check-then-read pair
    would leave open."""
    try:
        dir_fd = os.open(parent, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY)
    except FileNotFoundError:
        return _MISSING_PROJECT_STUB
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            _logger.warning(
                "sync_project_claude_md: %s is a symlink; refusing to follow "
                "— using missing-project stub instead",
                parent,
            )
            return _MISSING_PROJECT_STUB
        if exc.errno == errno.ENOTDIR:
            _logger.warning(
                "sync_project_claude_md: %s is not a directory; using "
                "missing-project stub instead",
                parent,
            )
            return _MISSING_PROJECT_STUB
        raise
    try:
        try:
            fd = os.open(
                name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=dir_fd,
            )
        except FileNotFoundError:
            return _MISSING_PROJECT_STUB
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                _logger.warning(
                    "sync_project_claude_md: %s/%s is a symlink; refusing to "
                    "follow — using missing-project stub instead",
                    parent,
                    name,
                )
                return _MISSING_PROJECT_STUB
            raise
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                _logger.warning(
                    "sync_project_claude_md: %s/%s is not a regular file "
                    "(mode=%o); using missing-project stub instead",
                    parent,
                    name,
                    st.st_mode,
                )
                return _MISSING_PROJECT_STUB
            with os.fdopen(fd, "r", encoding="utf-8") as f:
                fd = -1  # transferred to fdopen
                return f.read()
        finally:
            if fd != -1:
                try:
                    os.close(fd)
                except OSError:
                    pass
    finally:
        try:
            os.close(dir_fd)
        except OSError:
            pass


async def _git_capture(cwd: Path, *args: str) -> tuple[int, str, str]:
    """Run ``git *args`` in ``cwd`` and return ``(returncode, stdout, stderr)``.
    Async equivalent of ``subprocess.run(..., capture_output=True)`` — never
    raises on non-zero exit, lets the caller decide."""
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return (
        proc.returncode or 0,
        stdout.decode(errors="replace"),
        stderr.decode(errors="replace"),
    )


async def _is_tracked(worktree_path: Path, relpath: str) -> bool:
    """Return True when ``relpath`` is tracked in the worktree's git index."""
    rc, _, _ = await _git_capture(
        worktree_path, "ls-files", "--error-unmatch", "--", relpath
    )
    return rc == 0


async def _mark_skip_worktree(worktree_path: Path, relpath: str) -> None:
    """Set the skip-worktree bit so local edits to ``relpath`` never show in
    ``git status`` or get staged. Best-effort: log a warning on failure."""
    rc, _, stderr = await _git_capture(
        worktree_path, "update-index", "--skip-worktree", "--", relpath
    )
    if rc != 0:
        _logger.warning(
            "git update-index --skip-worktree %s failed in %s: %s",
            relpath,
            worktree_path,
            stderr.strip(),
        )


async def _add_to_local_exclude(worktree_path: Path, pattern: str) -> None:
    """Idempotently add ``pattern`` to ``info/exclude`` (shared between the
    main repo and any linked worktrees; git has no per-worktree exclude file).
    Resolved via ``git rev-parse --git-path info/exclude`` so the call still
    works in linked worktrees where ``.git`` is a file, not a directory.
    Rewrites the file only if its content actually changes."""
    exclude_path = await _resolve_info_exclude_path(worktree_path)
    if exclude_path is None:
        _logger.warning(
            "could not resolve .git/info/exclude for %s; %s may show as untracked",
            worktree_path,
            pattern,
        )
        return
    existing = (
        exclude_path.read_text(encoding="utf-8") if exclude_path.is_file() else ""
    )
    if pattern in existing.splitlines():
        return
    exclude_path.parent.mkdir(parents=True, exist_ok=True)
    sep = "" if (not existing or existing.endswith("\n")) else "\n"
    exclude_path.write_text(f"{existing}{sep}{pattern}\n", encoding="utf-8")


async def _purge_local_exclude_patterns(
    worktree_path: Path, patterns: tuple[str, ...]
) -> None:
    """Remove any line in ``patterns`` from ``info/exclude``. No-op when the
    file doesn't exist or none of the patterns are present.

    Called independently of the tracked/untracked branch so that a legacy
    entry written by an earlier (buggy) version of this code is cleaned up
    even when the current sync would take the skip-worktree branch."""
    exclude_path = await _resolve_info_exclude_path(worktree_path)
    if exclude_path is None or not exclude_path.is_file():
        return
    existing = exclude_path.read_text(encoding="utf-8")
    lines = existing.splitlines()
    pattern_set = set(patterns)
    new_lines = [ln for ln in lines if ln not in pattern_set]
    if new_lines == lines:
        return
    new_content = "\n".join(new_lines) + ("\n" if new_lines else "")
    exclude_path.write_text(new_content, encoding="utf-8")


async def _resolve_info_exclude_path(worktree_path: Path) -> Path | None:
    """Return the ``info/exclude`` path git reports for ``worktree_path``, or
    ``None`` on git failure. In linked worktrees this resolves to the main
    repo's ``.git/info/exclude`` (shared, not per-worktree)."""
    rc, stdout, _ = await _git_capture(
        worktree_path, "rev-parse", "--git-path", "info/exclude"
    )
    if rc != 0:
        return None
    relpath = stdout.strip()
    if not relpath:
        return None
    path = Path(relpath)
    return path if path.is_absolute() else worktree_path / path


async def _auto_lint(worktree_path: Path) -> list[str]:
    """Run ruff format and ruff check on the worktree.

    Auto-fixes what it can (format + fixable lint violations).
    Returns a list of remaining unfixable lint errors, empty if clean.
    Uses create_subprocess_exec (no shell) — args are fixed strings.
    """
    if not (worktree_path / "pyproject.toml").exists():
        return []

    # 1. Auto-format
    proc = await asyncio.create_subprocess_exec(
        "ruff",
        "format",
        ".",
        cwd=worktree_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        # SF-I2: ``ruff format`` failing means a broken toolchain or
        # config, not a stylistic issue — raise so the caller can't
        # treat the run as clean just because a later ``ruff check``
        # happened to pass.
        message = stdout.decode().strip() or f"ruff format rc={proc.returncode}"
        _logger.warning("ruff format failed (rc=%d): %s", proc.returncode, message)
        raise LintError([f"ruff format failed: {message}"])

    # 2. Auto-fix lint violations
    proc = await asyncio.create_subprocess_exec(
        "ruff",
        "check",
        "--fix",
        ".",
        cwd=worktree_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await proc.communicate()

    # 3. Check for remaining unfixable issues
    proc = await asyncio.create_subprocess_exec(
        "ruff",
        "check",
        ".",
        cwd=worktree_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode == 0:
        return []

    errors = stdout.decode().strip().splitlines()
    _logger.warning("ruff check found %d unfixable issues", len(errors))
    return errors


async def commit_worktree(worktree_path: Path, message: str) -> CommitResult:
    """Commit all changes in a worktree.

    Returns a :class:`CommitResult` carrying the new commit SHA (``None`` if
    there were no changes) and the deterministic code-quality metrics for the
    committed change. Raises ``LintError`` if there are unfixable lint
    violations.
    """
    lint_errors = await _auto_lint(worktree_path)
    if lint_errors:
        raise LintError(lint_errors)
    await _run_git(worktree_path, "add", "-A")

    # Check if there's anything staged
    proc = await asyncio.create_subprocess_exec(
        "git",
        "diff",
        "--cached",
        "--quiet",
        cwd=worktree_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()
    if proc.returncode == 0:
        return CommitResult(sha=None, metrics=None)  # Nothing staged

    # Code-quality signal for exactly the staged change (compared to HEAD,
    # which is still the pre-commit tip here). Signal only — a failure must
    # never block the commit, so it degrades to ``metrics=None``.
    metrics: ChangeMetrics | None
    try:
        metrics = await compute_change_metrics(worktree_path, base_ref="HEAD")
    except Exception:
        _logger.warning(
            "commit_worktree: code metrics failed for %s; committing without signal",
            worktree_path,
            exc_info=True,
        )
        metrics = None

    await _run_git(worktree_path, "commit", "-m", message)
    sha = await _run_git(worktree_path, "rev-parse", "HEAD")

    if metrics is not None:
        _logger.info(
            "commit %s code metrics: max_cc=%d%s ruff_findings=%d loc_delta=%+d%s",
            sha[:7],
            metrics.max_cc,
            f" ({metrics.max_cc_location})" if metrics.max_cc_location else "",
            metrics.ruff_findings,
            metrics.loc_delta,
            "  [FLAGGED]" if metrics.flagged else "",
        )
    return CommitResult(sha=sha, metrics=metrics)


async def remove_worktree(
    project_path: Path,
    ticket_id: str,
    *,
    keep_branch: bool = False,
) -> None:
    """Remove a git worktree, optionally preserving its branch for merge."""
    validate_safe_path_segment(ticket_id, "ticket_id")
    worktree_path = project_path / ".jig" / "worktrees" / ticket_id
    branch_name = f"jig/{ticket_id}"
    await _run_git(project_path, "worktree", "remove", str(worktree_path), "--force")
    if not keep_branch:
        try:
            await _run_git(project_path, "branch", "-D", branch_name)
        except RuntimeError:
            pass  # Branch may already be deleted


async def merge_dep_into_worktree(worktree_path: Path, dep_branch: str) -> None:
    """Merge a dependency branch into a worktree so the agent sees its code.

    Raises RuntimeError if the branch doesn't exist or the merge conflicts.
    """
    # Verify the branch exists before attempting merge
    await _run_git(worktree_path, "rev-parse", "--verify", dep_branch)
    await _run_git(
        worktree_path,
        "merge",
        dep_branch,
        "--no-edit",
        "-m",
        f"chore: merge dependency {dep_branch}",
    )


async def _run_cmd(cwd: Path, *args: str) -> str:
    """Run an arbitrary command and return stdout."""
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} failed: {stderr.decode().strip()}")
    return stdout.decode().strip()


# Serializes merge operations so parallel ticket completions don't race
# on the shared main repo checkout.
_merge_lock = asyncio.Lock()


async def merge_ticket(
    project_path: Path,
    ticket_id: str,
    base_branch: str,
    strategy: MergeStrategy,
) -> str:
    """Merge the ticket branch into the base branch.

    Returns a short description of what was done.
    """
    validate_safe_path_segment(ticket_id, "ticket_id")
    source_branch = f"jig/{ticket_id}"

    if strategy == MergeStrategy.FEATURE_BRANCH:
        feature_branch = f"feature/{ticket_id}"
        try:
            await _run_git(project_path, "branch", "-D", feature_branch)
        except RuntimeError:
            pass
        await _run_git(project_path, "branch", feature_branch, source_branch)
        return f"Created feature branch: {feature_branch}"

    if strategy == MergeStrategy.PR:
        feature_branch = f"feature/{ticket_id}"
        try:
            await _run_git(project_path, "branch", "-D", feature_branch)
        except RuntimeError:
            pass
        await _run_git(project_path, "branch", feature_branch, source_branch)
        try:
            await _run_git(project_path, "push", "-u", "origin", feature_branch)
            stdout = await _run_cmd(
                project_path,
                "gh",
                "pr",
                "create",
                "--base",
                base_branch,
                "--head",
                feature_branch,
                "--title",
                f"jig: {ticket_id}",
                "--body",
                f"Automated PR for ticket {ticket_id}",
            )
            return f"Created PR: {stdout}"
        except (RuntimeError, FileNotFoundError) as e:
            return f"Created feature branch: {feature_branch} (PR failed: {e})"

    # Direct or squash merge — serialize to prevent racing on the main repo.
    async with _merge_lock:
        return await _do_merge(
            project_path, ticket_id, source_branch, base_branch, strategy
        )


async def _do_merge(
    project_path: Path,
    ticket_id: str,
    source_branch: str,
    base_branch: str,
    strategy: MergeStrategy,
) -> str:
    """Perform the actual merge under the lock."""
    # Clean up any leftover dirty state from a previous failed merge.
    # Only ``merge --abort`` here — destroying the operator's
    # uncommitted work in the main checkout (the historical
    # ``reset --hard HEAD``) is too operationally hostile. If the
    # checkout is genuinely dirty for some reason other than an
    # aborted merge, we surface that as a clean refusal below
    # rather than silently overwrite. SEC-I5 in
    # v2-review-findings-security.md.
    try:
        await _run_git(project_path, "merge", "--abort")
    except RuntimeError:
        pass

    # Refuse to merge if the main checkout still has uncommitted
    # changes. The operator may have in-progress work; a hard reset
    # would lose it.
    status = await _run_git(project_path, "status", "--porcelain")
    if status.strip():
        raise RuntimeError(
            "main checkout has uncommitted changes; refusing to merge "
            f"ticket {ticket_id} until it is clean. "
            "Stash, commit, or discard the local changes first."
        )

    # Integrate the latest base_branch into the ticket branch BEFORE
    # we attempt to merge the ticket branch into base. Without this,
    # parallel tickets that touch overlapping files always conflict on
    # the second merge: ticket-A merges, base advances; ticket-B was
    # branched off the older base, so its merge sees a 3-way conflict
    # against ticket-A's overlapping changes. Pulling base into the
    # ticket branch first lets git's 3-way merge resolve cleanly when
    # changes are non-overlapping, and surfaces real overlap as a
    # MERGE_CONFLICT here (where the ticket branch is preserved for
    # manual or agent-driven resolution).
    worktree = project_path / ".jig" / "worktrees" / ticket_id
    if worktree.is_dir():
        wt_status = await _run_git(worktree, "status", "--porcelain")
        if wt_status.strip():
            # Agent left dirty state; auto-commit fence should have
            # caught this. Refuse rather than discard work.
            raise RuntimeError(
                f"ticket worktree {worktree} has uncommitted changes; "
                "refusing to integrate before merge. The auto-commit "
                "fence should have committed any leftover changes."
            )
        try:
            await _run_git(
                worktree,
                "merge",
                base_branch,
                "-m",
                f"chore: integrate {base_branch} into {source_branch}",
            )
        except RuntimeError as exc:
            _logger.warning(
                "merge conflict integrating %s into %s — aborting",
                base_branch,
                source_branch,
            )
            try:
                conflicted_raw = await _run_git(
                    worktree, "diff", "--name-only", "--diff-filter=U"
                )
                conflicted = [f for f in conflicted_raw.splitlines() if f]
            except RuntimeError:
                conflicted = []
            try:
                await _run_git(worktree, "merge", "--abort")
            except RuntimeError:
                pass
            raise MergeConflictError(ticket_id, source_branch, conflicted) from exc

    try:
        await _run_git(project_path, "checkout", base_branch)
    except RuntimeError:
        raise

    try:
        if strategy == MergeStrategy.SQUASH:
            await _run_git(project_path, "merge", "--squash", source_branch)
            # Check if the squash produced anything to commit
            proc = await asyncio.create_subprocess_exec(
                "git",
                "diff",
                "--cached",
                "--quiet",
                cwd=project_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            if proc.returncode == 0:
                return f"No new changes from {source_branch} (already on {base_branch})"
            await _run_git(project_path, "commit", "-m", f"feat: {ticket_id}")
            return f"Squash-merged {source_branch} into {base_branch}"
        else:
            # MergeStrategy.DIRECT
            await _run_git(
                project_path, "merge", source_branch, "-m", f"Merge {ticket_id}"
            )
            return f"Merged {source_branch} into {base_branch}"
    except RuntimeError as exc:
        # Merge conflict — abort and leave branch intact for manual
        # resolution, then raise a typed error so the orchestrator
        # can route to MERGE_CONFLICT instead of RESOLVED.
        _logger.warning("merge conflict for %s — aborting", ticket_id)
        try:
            conflicted_raw = await _run_git(
                project_path, "diff", "--name-only", "--diff-filter=U"
            )
            conflicted = [f for f in conflicted_raw.splitlines() if f]
        except RuntimeError:
            conflicted = []
        try:
            await _run_git(project_path, "merge", "--abort")
        except RuntimeError:
            await _run_git(project_path, "reset", "--hard", "HEAD")
        raise MergeConflictError(ticket_id, source_branch, conflicted) from exc
