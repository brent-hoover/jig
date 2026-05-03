"""Bones reviewer-set selection.

Track F's handoff note (recorded in ``docs/pm-workflow/design.md``
§"Reviewer federation — selection logic" defaults) requires that the
bones reviewer must NOT depend on a non-empty ``ticket.reviewer_set`` —
the bones Coordinator (Track F4) leaves ``reviewer_set`` empty on
materialized tickets, so contract-compliance has to default-on for the
whole bones layer.

The function lives in its own module (rather than as a static method
on ``ContractComplianceReviewer``) because the dispatch table grows in
MVP — the same call site will pick out any subset of the eight
federated reviewers per Planner-PM-emitted ``reviewer_set``. Keeping
it separate today means the synthetic operator's invocation site
doesn't change shape when MVP federation lands.
"""
from __future__ import annotations

from jig.ticket import Ticket

# Single bones reviewer id. The synthetic operator imports this constant
# rather than literal-stringing "contract-compliance" so a future rename
# is one edit.
BONES_REVIEWER_ID = "contract-compliance"


def should_run_for_bones(ticket: Ticket) -> list[str]:
    """Return the reviewer ids to run on ``ticket`` under bones rules.

    Three branches:

    - ``layer == "bones"`` and ``reviewer_set`` empty → default-on
      contract-compliance. This is the bones-Coordinator-materialized
      case; tickets land without a Planner-authored reviewer set.
    - ``reviewer_set`` non-empty → honour it as authored. The Planner
      doesn't run in bones, but the slot still respects an explicit
      operator override (e.g. a synthetic-operator scenario that
      hand-writes ``reviewer_set: []`` to opt OUT of review).
    - ``layer != "bones"`` and empty ``reviewer_set`` → return empty.
      MVP/Final layers without a Planner-authored set are operator
      error; bones doesn't paper over it by silently running the bones
      reviewer on a non-bones ticket.

    Distinct empty-list returns for "bones, no reviewers requested"
    versus "non-bones, no reviewer set authored" share the same shape;
    the synthetic operator can disambiguate via the input ticket if it
    needs to.
    """
    if ticket.reviewer_set:
        return list(ticket.reviewer_set)
    if ticket.layer == "bones":
        return [BONES_REVIEWER_ID]
    return []


__all__ = [
    "BONES_REVIEWER_ID",
    "should_run_for_bones",
]
