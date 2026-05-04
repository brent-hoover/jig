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

# Intent-layer reviewer (Track I MVP). Defaults on for MVP / final
# tickets so the federation runs both contract-compliance and intent
# enforcement; bones layer keeps just contract-compliance to honor
# the bones budget. Imported by name from ``intent_compliance.py`` to
# keep the rename surface to one edit.
INTENT_REVIEWER_ID = "intent-compliance"

# MVP / final default sets. ``contract-compliance`` is reused from the
# bones default — every ticket benefits from the diff/AC checks, not
# just bones tickets.
_MVP_FINAL_DEFAULTS: list[str] = [BONES_REVIEWER_ID, INTENT_REVIEWER_ID]


def should_run_for_bones(ticket: Ticket) -> list[str]:
    """Return the reviewer ids to run on ``ticket``.

    Despite the name (kept stable for callers), this is the federation-
    dispatch entry point for all layers; it grew past the bones-only
    scope when Track I MVP added the intent reviewer for MVP/final.

    Branches:

    - ``reviewer_set`` non-empty → honour it as authored (the Planner
      PM owns the set on MVP+). Empty reviewer_set with layer ==
      ``bones`` → default-on contract-compliance only (bones budget).
    - ``layer == "mvp"`` or ``"final"`` with empty reviewer_set →
      default to contract-compliance + intent-compliance. This is the
      MVP layer's federation floor.
    - ``layer`` unset and empty reviewer_set → return empty. The
      bones-Coordinator materializes tickets with ``layer="bones"``;
      a missing layer means we can't tell what defaults apply.

    Distinct empty-list returns for the same shape stay disambiguated
    via the input ticket if a caller needs it.
    """
    if ticket.reviewer_set:
        return list(ticket.reviewer_set)
    if ticket.layer == "bones":
        return [BONES_REVIEWER_ID]
    if ticket.layer in ("mvp", "final"):
        return list(_MVP_FINAL_DEFAULTS)
    return []


__all__ = [
    "BONES_REVIEWER_ID",
    "INTENT_REVIEWER_ID",
    "should_run_for_bones",
]
