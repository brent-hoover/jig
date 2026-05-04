"""Deprecated alias module — see ``jig.reviewers.dispatch``.

This module exists only so external imports of ``BONES_REVIEWER_ID``
or ``should_run_for_bones`` from ``jig.reviewers.bones_dispatch``
keep working through the rename. New code should import from
``jig.reviewers.dispatch`` (or the package re-export at
``jig.reviewers``).

Schedule for removal once all internal call sites are migrated.
"""
from __future__ import annotations

from jig.reviewers.dispatch import (
    BONES_REVIEWER_ID,
    CROSS_CUTTING_REVIEWER_ID,
    INTENT_REVIEWER_ID,
    SPEC_COMPLIANCE_REVIEWER_ID,
    select_reviewers_for_ticket,
    should_run_for_bones,
)

__all__ = [
    "BONES_REVIEWER_ID",
    "CROSS_CUTTING_REVIEWER_ID",
    "INTENT_REVIEWER_ID",
    "SPEC_COMPLIANCE_REVIEWER_ID",
    "select_reviewers_for_ticket",
    "should_run_for_bones",
]
