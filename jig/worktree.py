"""Git worktree management for agent isolation."""

import asyncio
import logging
from pathlib import Path

from jig.models import MergeStrategy

_logger = logging.getLogger(__name__)


class LintError(Exception):
    """Raised when unfixable lint violations remain after auto-fix."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__(f"{len(errors)} unfixable lint errors")


async def _run_git(cwd: Path, *args: str) -> str:
    """Run a git command and return stdout."""
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
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

    Returns the path to the worktree directory.
    """
    worktree_path = project_path / ".jig" / "worktrees" / ticket_id
    branch_name = f"jig/{ticket_id}"

    # Ensure at least one commit exists (worktrees require a valid ref)
    try:
        await _run_git(project_path, "rev-parse", "HEAD")
    except RuntimeError:
        await _run_git(
            project_path,
            "commit", "--allow-empty", "-m", "chore: initialize repository",
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
        "worktree", "add", "-b", branch_name,
        str(worktree_path), base_branch,
    )
    return worktree_path


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
        "ruff", "format", ".",
        cwd=worktree_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        _logger.warning("ruff format failed (rc=%d): %s", proc.returncode, stdout.decode().strip())

    # 2. Auto-fix lint violations
    proc = await asyncio.create_subprocess_exec(
        "ruff", "check", "--fix", ".",
        cwd=worktree_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await proc.communicate()

    # 3. Check for remaining unfixable issues
    proc = await asyncio.create_subprocess_exec(
        "ruff", "check", ".",
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


async def commit_worktree(worktree_path: Path, message: str) -> str | None:
    """Commit all changes in a worktree.

    Returns the commit SHA, or None if there were no changes.
    Raises ``LintError`` if there are unfixable lint violations.
    """
    lint_errors = await _auto_lint(worktree_path)
    if lint_errors:
        raise LintError(lint_errors)
    await _run_git(worktree_path, "add", "-A")

    # Check if there's anything staged
    proc = await asyncio.create_subprocess_exec(
        "git", "diff", "--cached", "--quiet",
        cwd=worktree_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()
    if proc.returncode == 0:
        return None  # Nothing staged

    await _run_git(worktree_path, "commit", "-m", message)
    sha = await _run_git(worktree_path, "rev-parse", "HEAD")
    return sha


async def remove_worktree(
    project_path: Path, ticket_id: str, *, keep_branch: bool = False,
) -> None:
    """Remove a git worktree, optionally preserving its branch for merge."""
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
    await _run_git(worktree_path, "merge", dep_branch, "--no-edit",
                   "-m", f"chore: merge dependency {dep_branch}")


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
                "gh", "pr", "create",
                "--base", base_branch,
                "--head", feature_branch,
                "--title", f"jig: {ticket_id}",
                "--body", f"Automated PR for ticket {ticket_id}",
            )
            return f"Created PR: {stdout}"
        except (RuntimeError, FileNotFoundError) as e:
            return f"Created feature branch: {feature_branch} (PR failed: {e})"

    # Direct or squash merge — serialize to prevent racing on the main repo.
    async with _merge_lock:
        return await _do_merge(project_path, ticket_id, source_branch, base_branch, strategy)


async def _do_merge(
    project_path: Path,
    ticket_id: str,
    source_branch: str,
    base_branch: str,
    strategy: MergeStrategy,
) -> str:
    """Perform the actual merge under the lock."""
    # Clean up any leftover dirty state from a previous failed merge
    try:
        await _run_git(project_path, "merge", "--abort")
    except RuntimeError:
        pass
    try:
        await _run_git(project_path, "reset", "--hard", "HEAD")
    except RuntimeError:
        pass

    try:
        await _run_git(project_path, "checkout", base_branch)
    except RuntimeError:
        raise

    try:
        if strategy == MergeStrategy.SQUASH:
            await _run_git(project_path, "merge", "--squash", source_branch)
            # Check if the squash produced anything to commit
            proc = await asyncio.create_subprocess_exec(
                "git", "diff", "--cached", "--quiet",
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
            await _run_git(project_path, "merge", source_branch, "-m", f"Merge {ticket_id}")
            return f"Merged {source_branch} into {base_branch}"
    except RuntimeError:
        # Merge conflict — abort and leave branch intact for manual resolution
        _logger.warning("merge conflict for %s — aborting", ticket_id)
        try:
            await _run_git(project_path, "merge", "--abort")
        except RuntimeError:
            await _run_git(project_path, "reset", "--hard", "HEAD")
        return f"Merge conflict for {source_branch} (branch preserved for manual merge)"
