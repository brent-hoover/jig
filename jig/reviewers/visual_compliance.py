"""VisualComplianceReviewer — Track D MVP basic mechanical version.

Per ``docs/v2.0/pm-workflow/design.md`` §"Reviewer federation — selection
logic": "Visual compliance — when ticket implements UI (has
``visual_references.wireframes`` set); tier: senior. Vision-based diff
between implementation screenshot and the wireframe; design-system
token / component check at MVP/Final layers; accessibility check at
Final layer."

For **MVP scope** this reviewer ships the **basic** mechanical version
(no vision, no LLM):

1. **Existence check** — for each screen-id in
   ``ticket.visual_references``, verify the file exists at
   ``.jig/spec/wireframes/<screen_id>.html``. Missing → CRITICAL
   ``wireframe-not-found`` comment.
2. **Linter check** — run ``lint_wireframe`` on each existing
   wireframe; any critical violation → CRITICAL ``wireframe-lint-failed``
   comment so the dev sees the structural problem before integrating.
3. **Reference check** — text-token approach mirroring
   contract-compliance. The dev's diff must include the screen-id
   string somewhere (file path, comment, identifier). Missing →
   IMPORTANT ``wireframe-not-referenced`` comment.

Vision-based screenshot diff and design-system token / component checks
are Final scope (deferred per the deliverable spec). Accessibility
checks land at Final layer.

Stateless. Mirrors ``ContractComplianceReviewer`` so the dispatch table
in ``jig.reviewers.dispatch`` can plug it in with the same shape.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from jig.reviewers.comment import (
    ReviewerComment,
    ReviewerCommentType,
    Severity,
)
from jig.spec_loader import wireframe_path
from jig.ticket import Ticket
from jig.wireframes.linter import LintSeverity, lint_wireframe


VISUAL_COMPLIANCE_REVIEWER_ID = "visual-compliance"


def _git_diff(worktree_path: Path, base_ref: str) -> str:
    """Return ``git diff <base_ref>..HEAD`` from the worktree.

    Returns the empty string in every failure mode (no git, no repo,
    no commits). The reviewer treats empty output as "no diff to check
    references against" — that surfaces only as the
    ``wireframe-not-referenced`` comment, which is consistent: an
    empty diff fails the reference check the same way a populated diff
    that doesn't mention the screen-id does. Mirrors the helper in
    ``contract_compliance.py`` so both reviewers have the same
    git-failure semantics.
    """
    if not worktree_path.is_dir():
        return ""
    try:
        proc = subprocess.run(
            ["git", "diff", f"{base_ref}..HEAD"],
            cwd=worktree_path,
            capture_output=True,
            check=False,
            text=True,
        )
    except (FileNotFoundError, OSError):
        return ""
    if proc.returncode != 0:
        try:
            fallback = subprocess.run(
                ["git", "diff", "HEAD"],
                cwd=worktree_path,
                capture_output=True,
                check=False,
                text=True,
            )
        except (FileNotFoundError, OSError):
            return ""
        if fallback.returncode != 0:
            return ""
        return fallback.stdout
    return proc.stdout


def _default_worktree_path(project_root: Path, ticket_id: str) -> Path:
    """Match the convention in ``jig.worktree.create_worktree``."""
    from jig.safe_path import validate_safe_path_segment

    validate_safe_path_segment(ticket_id, "ticket_id")
    return project_root / ".jig" / "worktrees" / ticket_id


class VisualComplianceReviewer:
    """Mechanical visual_compliance reviewer (Track D MVP).

    Stateless. Construct once and call ``review`` per ticket; no
    per-ticket setup. The class wrapper mirrors
    ``ContractComplianceReviewer`` so the dispatch table can plug it
    in with the same shape.
    """

    reviewer_id: str = VISUAL_COMPLIANCE_REVIEWER_ID

    async def review(
        self,
        ticket: Ticket,
        project_root: Path,
        *,
        worktree_path: Path | None = None,
        base_ref: str = "main",
    ) -> list[ReviewerComment]:
        """Run the basic visual-compliance checks. Empty list = pass.

        ``worktree_path`` defaults to the orchestrator-convention
        ``<project_root>/.jig/worktrees/<ticket.id>/``. ``base_ref``
        defaults to ``main`` matching the contract-compliance reviewer.

        Skips silently when ``ticket.visual_references`` is empty —
        the dispatch layer should already gate on this, but the
        reviewer's own no-op-on-empty branch makes a direct call
        from a test still safe.
        """
        comments: list[ReviewerComment] = []

        screens = list(ticket.visual_references)
        if not screens:
            # No wireframes referenced — nothing to verify. Per design
            # the reviewer is gated on non-empty visual_references; this
            # branch keeps a direct invocation safe.
            return comments

        wt = worktree_path or _default_worktree_path(project_root, ticket.id)
        diff = _git_diff(wt, base_ref)

        for screen_id in screens:
            wf_path = wireframe_path(project_root, screen_id)

            if not wf_path.is_file():
                comments.append(
                    ReviewerComment(
                        type=ReviewerCommentType.WIREFRAME_NOT_FOUND,
                        severity=Severity.CRITICAL,
                        reviewer=self.reviewer_id,
                        prose=(
                            f"Wireframe {screen_id!r} is referenced by "
                            f"this ticket but not found at "
                            f"{wf_path.relative_to(project_root)!s}. VD "
                            "discovery may not have authored it yet, or "
                            "the visual_references list on the ticket "
                            "may be stale."
                        ),
                        ticket_id=ticket.id,
                        file=str(wf_path.relative_to(project_root)),
                    )
                )
                continue

            html = wf_path.read_text()
            critical_lints = [
                e
                for e in lint_wireframe(html)
                if e.severity == LintSeverity.CRITICAL.value
            ]
            if critical_lints:
                # Surface the first failing message verbatim so the dev
                # sees the actionable detail; subsequent messages follow
                # via "(plus N more)". Bundling all messages into one
                # comment keeps the comment count proportional to the
                # *number of broken wireframes* rather than the number
                # of violations across them.
                head = critical_lints[0].message
                tail = (
                    f" (plus {len(critical_lints) - 1} more)"
                    if len(critical_lints) > 1
                    else ""
                )
                comments.append(
                    ReviewerComment(
                        type=ReviewerCommentType.WIREFRAME_LINT_FAILED,
                        severity=Severity.CRITICAL,
                        reviewer=self.reviewer_id,
                        prose=(
                            f"Wireframe {screen_id!r} fails the linter: "
                            f"{head}{tail}. Re-author via "
                            "vd_set_wireframe before re-submitting the "
                            "implementation."
                        ),
                        ticket_id=ticket.id,
                        file=str(wf_path.relative_to(project_root)),
                    )
                )

            # Reference check. Text-token approach mirroring contract-
            # compliance: the diff must mention the screen_id somewhere
            # (file path, comment, identifier). Diff might be empty
            # (no commits, no git) — that's a separate signal the
            # contract-compliance reviewer catches; we still emit our
            # own reference miss so the operator sees both signals.
            if screen_id not in diff:
                comments.append(
                    ReviewerComment(
                        type=ReviewerCommentType.WIREFRAME_NOT_REFERENCED,
                        severity=Severity.IMPORTANT,
                        reviewer=self.reviewer_id,
                        prose=(
                            f"The dev's diff does not mention wireframe "
                            f"{screen_id!r} anywhere. The implementation "
                            "should reference the wireframe (template "
                            "filename, screen-id constant, comment, "
                            "etc.) so the visual reviewer can trace "
                            "implementation back to design. (MVP uses "
                            "token matching; Final adds vision-based "
                            "screenshot diff.)"
                        ),
                        ticket_id=ticket.id,
                    )
                )

        return comments


__all__ = [
    "VISUAL_COMPLIANCE_REVIEWER_ID",
    "VisualComplianceReviewer",
]
