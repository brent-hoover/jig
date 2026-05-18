"""``prepare-commit-msg`` hook + worktree context file.

Step 4 of feature-work/review-routing/plan.md. Every commit made in a
ticket worktree gets ``Phase: <name>`` and ``Agent: <role>`` Git trailers
appended automatically. The future fix-loop router (plan step 7) reads
these trailers via
``git log --pretty=format:%(trailers:key=Phase,valueonly) -- <file>``
to determine which phase last touched a given file when multiple phases
declare overlapping ``writes:`` globs.

Two pieces:

- ``install_commit_msg_hook(worktree_path)`` — drop an executable
  ``prepare-commit-msg`` script into the worktree's ``.git/hooks/``.
- ``write_worktree_context(worktree_path, *, phase, agent)`` — write
  ``.jig/worktree.context`` with the two ``key=value`` lines the hook
  reads. The orchestrator calls this at every phase boundary.

The hook itself is pure bash (no jig CLI dependency) so it's robust to
operator-driven commits in environments where ``jig`` isn't on PATH.
"""

from __future__ import annotations

import stat
from pathlib import Path


# Sentinel makes hook ownership detectable without parsing — mirrors
# the convention in ``jig.hooks.per_commit``.
COMMIT_MSG_PROVENANCE_SENTINEL = (
    "# jig commit-msg provenance hook — appends Phase/Agent trailers"
)


_HOOK_SCRIPT = """#!/usr/bin/env bash
{sentinel}
# Reads <worktree>/.jig/worktree.context (key=value lines) and appends
# Phase: / Agent: Git trailers to the commit message via
# `git interpret-trailers` so the body-vs-trailer-block formatting and
# duplicate-detection are handled by git itself.
#
# No-op when the context file is missing or the trailer would be a duplicate.

set -eu

COMMIT_MSG_FILE="$1"

# Locate the worktree root. The hook lives at
# <worktree>/.git/hooks/prepare-commit-msg, but for linked worktrees
# .git is a file pointing into the main repo, so resolve via git itself.
if ! WORKTREE="$(git rev-parse --show-toplevel 2>/dev/null)"; then
    exit 0
fi

CONTEXT_FILE="$WORKTREE/.jig/worktree.context"
[ -f "$CONTEXT_FILE" ] || exit 0

# Read phase= and agent= lines from the context. Missing keys leave
# the variable empty; we skip empty trailers below.
PHASE=""
AGENT=""
while IFS='=' read -r key value; do
    case "$key" in
        phase) PHASE="$value" ;;
        agent) AGENT="$value" ;;
    esac
done < "$CONTEXT_FILE"

# Build the trailer args. `--if-exists doNothing` makes the append a
# no-op when an identical trailer is already present (idempotent on
# commit --amend and repeated runs).
ARGS=(--in-place --if-exists doNothing)
[ -n "$PHASE" ] && ARGS+=(--trailer "Phase: $PHASE")
[ -n "$AGENT" ] && ARGS+=(--trailer "Agent: $AGENT")

# If we have nothing to add (both fields empty), exit silently.
if [ ${{#ARGS[@]}} -le 2 ]; then
    exit 0
fi

# Failure of git interpret-trailers (e.g. git < 2.10, stripped-down build,
# or any transient error) must not abort the commit — the hook is
# best-effort metadata. Degrade silently.
git interpret-trailers "${{ARGS[@]}}" "$COMMIT_MSG_FILE" || exit 0
exit 0
""".format(sentinel=COMMIT_MSG_PROVENANCE_SENTINEL)


def _resolve_hooks_dir(worktree_path: Path) -> Path:
    """Return the real ``.git/hooks`` directory for a worktree.

    Linked worktrees store ``.git`` as a file pointing at
    ``<main-repo>/.git/worktrees/<name>/``; hooks live in the pointed-at
    directory. For non-worktree dirs (tests, fresh repos), the
    ``<worktree>/.git/hooks/`` form works directly. This is the same
    pattern used by ``jig.hooks.per_commit.install_per_commit_hook``.
    """
    git_path = worktree_path / ".git"
    if git_path.is_file():
        pointer = git_path.read_text().strip()
        if pointer.startswith("gitdir:"):
            gitdir = Path(pointer.split(":", 1)[1].strip())
            if not gitdir.is_absolute():
                gitdir = (worktree_path / gitdir).resolve()
            return gitdir / "hooks"
    return git_path / "hooks"


def install_commit_msg_hook(worktree_path: Path) -> Path:
    """Install the ``prepare-commit-msg`` hook for commit-msg provenance.

    Returns the absolute path to the installed hook. Idempotent —
    re-installation overwrites cleanly, since the script body is a pure
    constant (no per-worktree interpolation).
    """
    hooks_dir = _resolve_hooks_dir(worktree_path)
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook_path = hooks_dir / "prepare-commit-msg"
    hook_path.write_text(_HOOK_SCRIPT)
    current_mode = hook_path.stat().st_mode
    hook_path.chmod(current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return hook_path


def write_worktree_context(worktree_path: Path, *, phase: str, agent: str) -> Path:
    """Write ``<worktree>/.jig/worktree.context`` with the current phase
    + agent. Overwrites any previous content.

    The hook reads this file at commit time to know what trailers to
    append. The orchestrator calls this at every phase boundary so the
    trailers track the actual phase that produced each commit.
    """
    ctx_path = worktree_path / ".jig" / "worktree.context"
    ctx_path.parent.mkdir(parents=True, exist_ok=True)
    ctx_path.write_text(f"phase={phase}\nagent={agent}\n")
    return ctx_path


def is_commit_msg_provenance_hook(hook_path: Path) -> bool:
    """True when ``hook_path`` is a jig commit-msg provenance hook
    (sentinel match). Mirrors ``is_per_commit_hook``."""
    if not hook_path.is_file():
        return False
    try:
        text = hook_path.read_text()
    except (PermissionError, UnicodeDecodeError):
        return False
    return COMMIT_MSG_PROVENANCE_SENTINEL in text


__all__ = [
    "COMMIT_MSG_PROVENANCE_SENTINEL",
    "install_commit_msg_hook",
    "is_commit_msg_provenance_hook",
    "write_worktree_context",
]
