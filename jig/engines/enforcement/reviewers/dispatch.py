"""Reviewer federation dispatch — the new home for reviewer selection/dispatch.

Bones: re-exports ``jig.reviewers.dispatch`` so the Enforcement package owns this
surface. The physical move is deferred to MVP — moving it now would create a
``jig.reviewers/__init__`` <-> ``dispatch`` import cycle (the package init imports
from dispatch, and dispatch imports ``jig.reviewers.comment``). MVP relocates the
federation here and wires Build to invoke it through the ``Review`` contract.
"""

from __future__ import annotations

from jig.reviewers.dispatch import (
    LlmReviewerPending,
    dispatch_for_cadence,
    dispatch_with_llm_spawn,
    known_llm_reviewer_ids,
    promote_dev_tier,
    select_reviewers_for_ticket,
)

__all__ = [
    "LlmReviewerPending",
    "dispatch_for_cadence",
    "dispatch_with_llm_spawn",
    "known_llm_reviewer_ids",
    "promote_dev_tier",
    "select_reviewers_for_ticket",
]
