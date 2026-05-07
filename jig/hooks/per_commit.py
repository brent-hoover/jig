"""Per-commit reviewer hook installer (Track G MVP follow-on).

Per ``docs/v2.0/pm-workflow/design.md`` section "Two-cadence review":
the per-commit cadence fires within seconds of ``git commit``,
running the mechanical reviewer subset (contract-compliance,
cross-cutting-policy, spec-compliance) so the dev agent gets a
fast feedback loop on contract drift before compounding mistakes.

This module installs a tiny ``post-commit`` hook into the ticket's
worktree that delegates to ``jig.hooks.per_commit_runner``. We use
``post-commit`` rather than ``pre-commit`` because the design is
explicit: the per-commit reviewer is informational, not blocking
(critical findings emit ``PerCommitCheckFailed`` analytics + print
to stderr, but the commit already succeeded). A blocking pre-commit
would either freeze the dev agent or get bypassed via ``--no-verify``;
post-commit + analytics is the design's chosen tradeoff.

The ``jig/hooks/__init__.py`` module owns the human-facing
pre-commit/pre-push/commit-msg flow (Phase 5 Task I). The two
co-exist: human-facing hooks are repo-wide (under
``<git_common_dir>/hooks/``); per-commit reviewer hooks are
per-worktree (under ``<worktree>/.git/hooks/``).
"""

from __future__ import annotations

import shlex
import stat
from pathlib import Path

from jig.safe_path import validate_safe_path_segment

# Sentinel makes hook ownership detectable without parsing — mirrors
# the convention in ``jig/hooks/__init__.py``.
PER_COMMIT_SENTINEL = (
    "# jig per-commit reviewer hook — safe to remove via "
    "'jig hooks uninstall' or by deleting this file"
)


def _build_hook_script(ticket_id: str) -> str:
    """Return the post-commit hook body for a ticket worktree.

    ``ticket_id`` is interpolated into a shell command, so we run it
    through both the safe-path validator (rejects ``..``, ``/``, NUL,
    etc.) and ``shlex.quote`` for defence in depth — orchestrator-built
    ticket ids already pass the validator, but the public installer
    accepts arbitrary worktree names. SEC-I4 in
    v2-review-findings-security.md.
    """
    validate_safe_path_segment(ticket_id, "ticket_id")
    safe_id = shlex.quote(ticket_id)
    return f"""#!/usr/bin/env bash
{PER_COMMIT_SENTINEL}
# Track G MVP: dispatches the mechanical reviewer subset on each
# commit in this ticket worktree. Informational, non-blocking — exit
# is forced to 0 even when reviewers flag critical issues.

if ! command -v jig >/dev/null 2>&1; then
  # Without jig on PATH the hook can't run; stay silent to avoid
  # noise in environments where the dev agent intentionally runs
  # outside the jig CLI (e.g. operator-driven manual commits).
  exit 0
fi

jig per-commit-review --ticket {safe_id} || true
exit 0
"""


def install_per_commit_hook(worktree_path: Path) -> Path:
    """Install the per-commit reviewer hook into ``worktree_path``.

    Returns the absolute path to the installed hook script. Idempotent:
    re-installation overwrites the existing file (the script body is a
    pure function of the ticket id, so any drift would be a bug — we
    prefer a clean overwrite over a stale-detection branch).

    The ticket id is derived from the worktree's directory name, which
    matches the orchestrator's convention
    (``<project>/.jig/worktrees/<ticket_id>/``). Worktrees with a
    different layout get the directory name as the id; the runner
    tolerates a missing-ticket lookup gracefully.
    """
    hooks_dir = worktree_path / ".git" / "hooks"
    # In a worktree, ``.git`` is usually a file pointing at the
    # main repo's worktree-state directory. Resolve the actual
    # hooks dir via ``git rev-parse --git-path hooks`` would be
    # ideal, but for MVP a direct path resolution is enough: the
    # orchestrator's ``create_worktree`` always lands a real
    # ``.git`` dir for the worktree, with hooks at
    # ``<git_common_dir>/worktrees/<name>/hooks``. Falling back to
    # creating the local dir if needed keeps tests + ad-hoc setups
    # working.
    if not hooks_dir.parent.exists():
        # Not a real git worktree; create a dummy hooks dir for
        # the test harness that constructs an isolated tree.
        hooks_dir.mkdir(parents=True, exist_ok=True)
    elif (worktree_path / ".git").is_file():
        # Real worktree — read the gitdir pointer.
        gitdir_pointer = (worktree_path / ".git").read_text().strip()
        if gitdir_pointer.startswith("gitdir:"):
            gitdir = Path(gitdir_pointer.split(":", 1)[1].strip())
            if not gitdir.is_absolute():
                gitdir = (worktree_path / gitdir).resolve()
            hooks_dir = gitdir / "hooks"

    hooks_dir.mkdir(parents=True, exist_ok=True)
    ticket_id = worktree_path.name

    hook_path = hooks_dir / "post-commit"
    hook_path.write_text(_build_hook_script(ticket_id))
    # Make the script executable (rwxr-xr-x). git ignores hooks that
    # aren't executable.
    current_mode = hook_path.stat().st_mode
    hook_path.chmod(
        current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    )
    return hook_path


def is_per_commit_hook(hook_path: Path) -> bool:
    """True when ``hook_path`` is a jig per-commit hook (sentinel match)."""
    if not hook_path.is_file():
        return False
    try:
        text = hook_path.read_text()
    except (PermissionError, UnicodeDecodeError):
        return False
    return PER_COMMIT_SENTINEL in text


def is_executable(hook_path: Path) -> bool:
    """True when ``hook_path`` has any executable bit set."""
    if not hook_path.is_file():
        return False
    return bool(hook_path.stat().st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))


__all__ = [
    "PER_COMMIT_SENTINEL",
    "install_per_commit_hook",
    "is_executable",
    "is_per_commit_hook",
]
