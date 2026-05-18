"""FullVisualComplianceReviewer — Track D Final extension.

Wraps the MVP ``VisualComplianceReviewer`` (existence + linter +
reference-token check) and adds the vision-based screenshot diff
described in ``docs/v2.0/visual-design/design.md`` §"Visual compliance
reviewer" + ``docs/v2.0/implementation/v2-plan.md`` Track D Final row.

The Final-layer flow per visual reference:

1. Run the MVP reviewer's mechanical checks. If the wireframe is
   missing the MVP reviewer already emitted a ``wireframe-not-found``
   critical; we surface that and skip the vision step (no point
   diffing against a missing reference).
2. Look for a captured screenshot at
   ``<screenshots_dir>/<screen_id>.png``. Missing → IMPORTANT
   ``screenshot-missing`` comment so the operator knows to capture
   one. The capture itself is operator-driven for v2 Final
   (Playwright/Selenium automation is v2.x).
3. Both present → call the injected ``VisionProviderProtocol`` with
   the two image byte-streams + a context string identifying the
   screen + ticket. Map each ``VisualDifference`` returned into a
   ``visual-vision-diff`` ReviewerComment carrying the kind +
   description + (optional) location_hint as prose, and the
   provider-supplied severity.

The vision provider itself is the **only** LLM in this code path; the
reviewer stays deterministic given a fixed provider response. Tests
inject ``StubVisionProvider`` with a pre-canned ``VisionDiffResult``
for full coverage of every difference kind.

The reviewer is invoked **explicitly** (not via the standard
``dispatch.py`` per-cadence hook) because the vision provider is
operator-wired — the dispatch table can't construct one without
configuration. Final-layer ticket flows wire the call inline once the
operator has a provider attached; the synthetic-operator scenario
exercises the path end-to-end with the stub.
"""

from __future__ import annotations

from pathlib import Path

from jig.reviewers.comment import (
    ReviewerComment,
    ReviewerCommentType,
    Severity,
)
from jig.reviewers.visual_compliance import (
    VISUAL_COMPLIANCE_REVIEWER_ID,
    VisualComplianceReviewer,
)
from jig.reviewers.vision_provider import (
    VisionProviderProtocol,
    VisualDifference,
)
from jig.spec_loader import wireframe_path
from jig.ticket import Ticket

__all__ = [
    "FullVisualComplianceReviewer",
]


class FullVisualComplianceReviewer:
    """Final-layer visual-compliance reviewer with injectable vision.

    Stateless. Mirrors the construction shape of the MVP reviewer so
    the Final-layer dispatcher can plug it in identically once the
    operator wires a vision provider.
    """

    reviewer_id: str = VISUAL_COMPLIANCE_REVIEWER_ID

    def __init__(self, mvp: VisualComplianceReviewer | None = None) -> None:
        # Compose rather than inherit — the MVP reviewer's signature is
        # stable and the Final layer only adds steps after the MVP
        # checks complete; injection lets a test stub the MVP if it
        # ever needs to (none today).
        self._mvp = mvp or VisualComplianceReviewer()

    async def review_full(
        self,
        ticket: Ticket,
        project_root: Path,
        vision: VisionProviderProtocol,
        screenshots_dir: Path,
        *,
        worktree_path: Path | None = None,
        base_ref: str = "main",
    ) -> list[ReviewerComment]:
        """Run MVP checks + vision diff for each visual reference.

        ``screenshots_dir`` is the operator-controlled path holding
        captured PNGs (one per screen_id). For Final scope the operator
        captures these manually; v2.x ships Playwright integration.

        Returns the combined comment list — MVP comments first
        (preserve their ordering), then per-screen vision comments.
        Empty list = full pass.
        """
        comments = await self._mvp.review(
            ticket,
            project_root,
            worktree_path=worktree_path,
            base_ref=base_ref,
        )

        # Index MVP comments by screen_id so we can short-circuit the
        # vision step for screens whose wireframe is already missing —
        # diffing against a non-existent reference makes no sense.
        missing_wireframes: set[str] = set()
        for c in comments:
            if c.type == ReviewerCommentType.WIREFRAME_NOT_FOUND.value:
                # Recover the screen_id from the prose: it's the only
                # quoted token. Cheap-but-stable: the MVP reviewer
                # emits "Wireframe 'X' is referenced..." consistently.
                for screen_id in ticket.visual_references:
                    if f"{screen_id!r}" in c.prose:
                        missing_wireframes.add(screen_id)

        for screen_id in ticket.visual_references:
            if screen_id in missing_wireframes:
                continue

            wf_path = wireframe_path(project_root, screen_id)
            screenshot = screenshots_dir / f"{screen_id}.png"
            if not screenshot.is_file():
                comments.append(
                    ReviewerComment(
                        type=ReviewerCommentType.SCREENSHOT_MISSING,
                        severity=Severity.IMPORTANT,
                        reviewer=self.reviewer_id,
                        prose=(
                            f"No implementation screenshot found at "
                            f"{screenshot} for wireframe {screen_id!r}. "
                            "Capture one (manually for now; v2.x will "
                            "ship Playwright capture) and re-run the "
                            "Final-layer visual review so the vision "
                            "provider can diff implementation against "
                            "design."
                        ),
                        ticket_id=ticket.id,
                    )
                )
                continue

            wf_bytes = wf_path.read_bytes()
            shot_bytes = screenshot.read_bytes()
            context = (
                f"ticket={ticket.id} screen_id={screen_id} wireframe={wf_path.name}"
            )
            result = await vision.compare_images(
                reference=wf_bytes,
                candidate=shot_bytes,
                context=context,
            )
            for diff in result.differences:
                comments.append(_diff_to_comment(diff, ticket.id, screen_id))

        return comments


def _diff_to_comment(
    diff: VisualDifference, ticket_id: str, screen_id: str
) -> ReviewerComment:
    """Map one vision-diff entry into a ReviewerComment.

    Severity passes through as-is from the provider — it's the
    domain-best signal for "is this layout-shift load-bearing or
    cosmetic". The kind is stamped into the prose so the operator
    sees the category alongside the description.
    """
    location = f" (location: {diff.location_hint})" if diff.location_hint else ""
    prose = (
        f"[{diff.kind}] {diff.description}{location} "
        f"— wireframe {screen_id!r} vs implementation screenshot."
    )
    severity = Severity(diff.severity)
    return ReviewerComment(
        type=ReviewerCommentType.VISUAL_VISION_DIFF,
        severity=severity,
        reviewer=VISUAL_COMPLIANCE_REVIEWER_ID,
        prose=prose,
        ticket_id=ticket_id,
        # Anchor the comment on the wireframe so a critical finding
        # tells the operator which artifact to start from.
        file=screen_id,
    )
