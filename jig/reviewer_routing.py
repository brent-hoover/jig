"""Per-finding fix-loop router for the review federation.

Step 7 of feature-work/review-routing/plan.md. Replaces the hard-coded
``_write_roles = {"dev"}`` selection in ``orchestrator._find_fix_phase``
with per-finding routing that consults, in priority order:

1. ``ReviewerComment.target_role`` — when a reviewer flags a finding
   as belonging to a role above the writing layer (spec is wrong, an
   architecture decision needs revisiting), the comment carries an
   explicit ``target_role`` override.
2. ``PhaseConfig.writes`` globs — map ``comment.file`` to the phase
   that owns that path.
3. Commit-trailer tie-break — when multiple phases declare overlapping
   ``writes:`` globs (e.g. dev modifies a fixture under tests/),
   ``git log --pretty=format:%(trailers:key=Phase) -- <file>``
   identifies the phase that most recently touched it.
4. Fallback — most-recent dev phase before the blocked phase, flagged
   ``unowned-finding`` so the operator can see the file→role mapping
   is incomplete.

These helpers ship in this PR; step 8 swaps the orchestrator's
``_find_fix_phase`` call site over to ``_route_blocking_comments``.
"""

from __future__ import annotations

import fnmatch
import logging
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jig.models import WorkflowConfig
    from jig.reviewers.comment import ReviewerComment

_logger = logging.getLogger(__name__)


def _most_recent_phase_with_role(
    workflow: "WorkflowConfig", blocked_phase_idx: int, role: str
) -> int | None:
    """Return the index of the most recent phase with ``role`` before
    ``blocked_phase_idx``. ``None`` when no such phase exists.

    Walks backward from ``blocked_phase_idx - 1`` so the blocked phase
    itself is excluded — the router must never route back to the phase
    that just failed.
    """
    for idx in range(blocked_phase_idx - 1, -1, -1):
        if workflow.phases[idx].role == role:
            return idx
    return None


def _matches_any(file: str, globs: list[str]) -> bool:
    """True when ``file`` matches at least one of the globs. Uses
    ``fnmatch`` so ``**`` recursion + ``*`` single-segment patterns
    behave as a developer expects from ``writes:`` declarations."""
    return any(fnmatch.fnmatch(file, g) for g in globs)


def _last_touching_phase(worktree_path: Path, file: str) -> str | None:
    """Return the ``Phase:`` trailer value of the most recent commit
    that touched ``file`` in the worktree, or ``None`` when no such
    commit exists or carries the trailer.

    Reads commit-msg provenance written by the ``prepare-commit-msg``
    hook installed by ``jig.hooks.commit_msg_provenance`` (review-routing
    step 4). Operator-authored or pre-hook commits have no trailer; this
    returns ``None`` for them so the caller can fall through to a
    deterministic tie-break.
    """
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(worktree_path),
                "log",
                "--pretty=format:%(trailers:key=Phase,valueonly)",
                "--",
                file,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except (FileNotFoundError, OSError):
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        value = line.strip()
        if value:
            return value
    return None


def _route_one(
    workflow: "WorkflowConfig",
    blocked_phase_idx: int,
    comment: "ReviewerComment",
    worktree_path: Path,
) -> tuple[int | None, str]:
    """Route a single blocking comment to a target phase.

    Returns ``(target_phase_idx, reason)``. ``target_phase_idx`` is
    ``None`` when no route is possible (no matching glob and no dev
    phase to fall back on). ``reason`` is a short tag the orchestrator
    surfaces in thread notes + analytics so operators can see why a
    given phase was selected for retry.
    """
    # 1. Reviewer-declared target role overrides file-based routing.
    target_role = getattr(comment, "target_role", None)
    if target_role:
        idx = _most_recent_phase_with_role(workflow, blocked_phase_idx, target_role)
        if idx is not None:
            return idx, f"target_role={target_role}"
        # Unknown role — log and fall through to glob routing so a
        # reviewer hallucinating a role name degrades gracefully.
        _logger.warning(
            "_route_one: unknown target_role=%s on comment from %s; falling through",
            target_role,
            comment.reviewer,
        )

    # 2. File-glob routing. Collect every phase whose ``writes:`` glob
    # matches; iterate backward so single-match candidates come back in
    # most-recent-first order naturally.
    if comment.file:
        candidates: list[int] = [
            idx
            for idx in range(blocked_phase_idx - 1, -1, -1)
            if _matches_any(comment.file, workflow.phases[idx].writes)
        ]
        if len(candidates) == 1:
            (idx,) = candidates
            return idx, f"writes-glob {workflow.phases[idx].name}"
        if len(candidates) > 1:
            # Multi-match (shared fixture etc.). Tie-break via commit
            # trailers: walk git log for the file and pick the phase
            # whose trailer matches one of our candidates.
            last_phase = _last_touching_phase(worktree_path, comment.file)
            if last_phase is not None:
                matched = [
                    idx for idx in candidates if workflow.phases[idx].name == last_phase
                ]
                if matched:
                    return matched[0], f"last-touched {last_phase}"
            # No parseable trailer (or trailer doesn't match any
            # candidate). Fall through to the workflow-earliest
            # candidate so retries walk forward through the workflow.
            return candidates[-1], "ambiguous-ownership"

    # 3. Fallback: route to the most-recent dev phase. Marks the case
    # as "unowned-finding" so the operator can see the file→role
    # mapping is incomplete (typo in ``writes:`` or genuinely
    # cross-cutting file with no clear owner).
    idx = _most_recent_phase_with_role(workflow, blocked_phase_idx, "dev")
    if idx is not None:
        return idx, "unowned-finding"
    return None, "no-route"


def _route_blocking_comments(
    workflow: "WorkflowConfig",
    blocked_phase_idx: int,
    comments: list["ReviewerComment"],
    worktree_path: Path,
) -> tuple[int, str] | None:
    """Top-level router. Examines each blocking comment, determines its
    owning phase, and returns the **earliest** such phase before
    ``blocked_phase_idx``.

    Earliest so retries walk forward through the workflow — never
    re-run a later phase before an earlier one that has unresolved
    findings. Returns ``None`` when no comment routes anywhere, leaving
    the caller (the phase loop's fix path) to handle the no-route case
    (typically: fail the ticket).

    The returned ``reason`` is the union of every individual comment's
    route reason, joined with ``"; "`` and deduplicated, so the
    operator sees the full picture in the thread note rather than just
    the chosen phase's reason.
    """
    targets: list[int] = []
    reasons: list[str] = []
    for c in comments:
        idx, reason = _route_one(workflow, blocked_phase_idx, c, worktree_path)
        if idx is not None:
            targets.append(idx)
            reasons.append(reason)
    if not targets:
        return None
    chosen = min(targets)
    return chosen, "; ".join(sorted(set(reasons)))


__all__ = [
    "_last_touching_phase",
    "_most_recent_phase_with_role",
    "_route_blocking_comments",
    "_route_one",
]
