"""Reviewer-set selection for ticket-scoped review dispatch.

Originally bones-only (``bones_dispatch.should_run_for_bones``); now the
federation-wide dispatch entry point. Picks the set of reviewer ids to
run for a ticket based on its ``layer`` and ``reviewer_set``.

Defaults policy:

- ``reviewer_set`` non-empty → honour it as authored (the Planner PM
  owns the set on MVP+).
- Empty ``reviewer_set`` with ``layer == "bones"`` → contract-compliance
  only (bones budget).
- Empty ``reviewer_set`` with ``layer in {"mvp", "final"}`` → the
  shipped MVP/final defaults (contract + intent for now; cross-cutting
  + spec join in subsequent Track G MVP commits).
- Empty ``reviewer_set`` with ``layer`` unset → empty list (we can't
  tell what defaults apply without the layer).

This module deliberately keeps the file name distinct from the per-
reviewer modules; the name ``dispatch`` is the single place a caller
goes to ask "what runs on this ticket?".
"""
from __future__ import annotations

from jig.ticket import Ticket

# Reviewer ids. The constants live here (rather than in the per-reviewer
# modules) so the dispatch table can reference them without a circular
# import — the reviewers import ``ReviewerComment`` from ``comment.py``;
# the dispatch imports the reviewers; the constants stay above both.
BONES_REVIEWER_ID = "contract-compliance"
INTENT_REVIEWER_ID = "intent-compliance"
CROSS_CUTTING_REVIEWER_ID = "cross-cutting-policy"
SPEC_COMPLIANCE_REVIEWER_ID = "spec-compliance"

# Bones layer default-on set. Cross-cutting policies are universal rules
# (PII, secrets, no-direct-cross-module-db) per design §"Reviewer
# federation — selection logic" — they apply at every layer including
# bones, so cross-cutting joins contract-compliance in the bones
# default-on subset.
_BONES_DEFAULTS: list[str] = [BONES_REVIEWER_ID, CROSS_CUTTING_REVIEWER_ID]

# MVP / final default set. ``contract-compliance`` is reused from the
# bones default — every ticket benefits from the diff/AC checks, not
# just bones tickets. Intent-compliance, cross-cutting-policy, and
# spec-compliance join from MVP onward.
_MVP_FINAL_DEFAULTS: list[str] = [
    BONES_REVIEWER_ID,
    INTENT_REVIEWER_ID,
    CROSS_CUTTING_REVIEWER_ID,
    SPEC_COMPLIANCE_REVIEWER_ID,
]


def select_reviewers_for_ticket(ticket: Ticket) -> list[str]:
    """Return the reviewer ids to run on ``ticket``.

    Branches:

    - ``reviewer_set`` non-empty → honour it as authored (the Planner
      PM owns the set on MVP+).
    - ``layer == "bones"`` with empty reviewer_set → default-on
      contract-compliance only (bones budget).
    - ``layer in ("mvp", "final")`` with empty reviewer_set → the
      MVP/final mechanical defaults.
    - ``layer`` unset and empty reviewer_set → empty list. The
      Coordinator materializes tickets with ``layer="bones"``; a
      missing layer means we can't tell what defaults apply.
    """
    if ticket.reviewer_set:
        return list(ticket.reviewer_set)
    if ticket.layer == "bones":
        return list(_BONES_DEFAULTS)
    if ticket.layer in ("mvp", "final"):
        return list(_MVP_FINAL_DEFAULTS)
    return []


# Backward-compatible alias. The bones-era name keeps working for the
# small handful of external callers; new code should use
# ``select_reviewers_for_ticket``.
should_run_for_bones = select_reviewers_for_ticket


__all__ = [
    "BONES_REVIEWER_ID",
    "CROSS_CUTTING_REVIEWER_ID",
    "INTENT_REVIEWER_ID",
    "SPEC_COMPLIANCE_REVIEWER_ID",
    "select_reviewers_for_ticket",
    "should_run_for_bones",
]
