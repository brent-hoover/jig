"""TracerPreservationReviewer — flags when a ticket touches tracer-covered nodes.

Phase 5 item #13. Mechanical (no LLM). When a ticket's directly-touched nodes
include a module or API that a tracer covers, the reviewer emits a warning so
the agent knows to re-run (or at minimum verify) the tracer smoke before
resolving the ticket.

Fires at end-of-ticket cadence. No-ops when:
  - No architecture.yaml exists (no graph to query).
  - No tracers are authored under ``.jig/spec/tracers/``.
  - The ticket has no graph touches.
  - No tracer covers any touched node.
"""

from __future__ import annotations

from pathlib import Path

from jig.reviewers.comment import ReviewerComment, ReviewerCommentType, Severity
from jig.ticket import Ticket

REVIEWER_ID = "tracer-preservation"


class TracerPreservationReviewer:
    """Flags tickets whose changes overlap with a tracer's covered nodes."""

    async def review(
        self,
        ticket: Ticket,
        project_root: Path,
    ) -> list[ReviewerComment]:
        arch_path = project_root / ".jig" / "spec" / "architecture.yaml"
        if not arch_path.exists():
            return []

        from jig.spec_loader import load_all_tracers
        from jig.graph.derive import build_graph, ticket_impact

        try:
            tracers = load_all_tracers(project_root)
        except Exception:
            return []

        if not tracers:
            return []

        try:
            graph = build_graph(project_root)
            impact = ticket_impact(graph, ticket.id, depth=0)
        except Exception:
            return []

        if not impact.touched:
            return []

        touched_ids = {n.id for n in impact.touched}

        comments: list[ReviewerComment] = []
        for tr in tracers:
            # Check whether any node covered by this tracer is in the touched set.
            covered: set[str] = set()
            for e in graph.edges:
                if e.src == f"tracer:{tr.id}" and e.kind == "covers":
                    covered.add(e.dst)

            overlap = touched_ids & covered
            if not overlap:
                continue

            overlap_names = sorted(nid.split(":", 1)[-1] for nid in overlap)
            comments.append(
                ReviewerComment(
                    reviewer=REVIEWER_ID,
                    type=ReviewerCommentType.TRACER_PRESERVATION_RISK,
                    severity=Severity.NOTABLE,
                    prose=(
                        f"Tracer {tr.id!r} covers node(s) touched by this "
                        f"ticket: {', '.join(overlap_names)}. "
                        "Re-run the tracer smoke to confirm it still passes."
                    ),
                    contract_uri=f"project://spec/tracers/{tr.id}",
                    confidence=1.0,
                )
            )

        return comments


__all__ = ["TracerPreservationReviewer", "REVIEWER_ID"]
