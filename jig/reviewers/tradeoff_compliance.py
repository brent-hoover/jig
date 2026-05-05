"""TradeoffComplianceReviewer — flags tickets that re-add deferred work.

Mechanical (no LLM). Loads `.jig/spec/tradeoffs.yaml` and checks whether any
of the ticket's capability_ids appear in a tradeoff whose deferred_to layer
is *later* than the ticket's own layer.

Example: a ticket at layer="bones" references capability "caching". A tradeoff
says "no caching at bones; deferred_to=mvp". The reviewer flags this as notable
— it might be intentional (PO changed their mind) but should be explicit.

Fires at end-of-ticket cadence alongside the other mechanical reviewers.
No-ops when the ledger is empty or the ticket has no capability_ids.
"""
from __future__ import annotations

from pathlib import Path

from jig.reviewers.comment import ReviewerComment, ReviewerCommentType, Severity
from jig.ticket import Ticket
from jig.tradeoff_store import load_ledger

_LAYER_ORDER = {"bones": 0, "mvp": 1, "final": 2}


def _layer_index(layer: str | None) -> int:
    return _LAYER_ORDER.get(layer or "", -1)


class TradeoffComplianceReviewer:
    """Flags capability-id overlaps with tradeoffs deferred beyond the ticket's layer."""

    async def review(
        self,
        ticket: Ticket,
        project_root: Path,
    ) -> list[ReviewerComment]:
        if not ticket.capability_ids:
            return []

        ledger = load_ledger(project_root)
        if not ledger.tradeoffs:
            return []

        ticket_layer_idx = _layer_index(ticket.layer)
        comments: list[ReviewerComment] = []

        for cap_id in ticket.capability_ids:
            for tradeoff in ledger.for_capability(cap_id):
                deferred_idx = _layer_index(tradeoff.deferred_to)
                if deferred_idx < 0:
                    # deferred_to="never" — always flag
                    deferred_idx = 99

                if ticket_layer_idx >= deferred_idx:
                    # Ticket layer is at or beyond when the work was expected
                    # — this is the intended landing zone, not a violation.
                    continue

                # Ticket is implementing something deferred to a later layer.
                deferred_items = "; ".join(tradeoff.deferred) or "(see rationale)"
                comments.append(
                    ReviewerComment(
                        type=ReviewerCommentType.DEFERRED_WORK_REINTRODUCED,
                        severity=Severity.NOTABLE,
                        reviewer="tradeoff-compliance",
                        prose=(
                            f"Ticket references capability '{cap_id}' but tradeoff "
                            f"'{tradeoff.id}' deferred this work to {tradeoff.deferred_to!r}. "
                            f"Deferred: {deferred_items}. "
                            f"Rationale: {tradeoff.rationale}. "
                            "If this is intentional, update the tradeoff ledger "
                            "(.jig/spec/tradeoffs.yaml) or get PO sign-off."
                        ),
                        contract_uri=f"project://spec/tradeoffs/{tradeoff.id}",
                    )
                )

        return comments
