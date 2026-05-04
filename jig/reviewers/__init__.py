"""Reviewer federation — Track G of the v2 implementation plan.

The full design splits review into eight federated agents (contract
compliance, cross-cutting policy, spec compliance, pattern conformance,
error handling, test adequacy, security, performance, architectural,
visual compliance). See ``docs/pm-workflow/design.md`` §"Reviewer
federation — selection logic" for the full taxonomy.

Currently shipped (mechanical, deterministic, no LLM):

- ``ContractComplianceReviewer`` — bones contract-compliance (Track G2).
- ``IntentComplianceReviewer`` — intent-layer enforcement (Track I MVP).
- ``CrossCuttingPolicyReviewer`` — universal-rule enforcement (Track G MVP).

Additional mechanical reviewers (spec-compliance) and the two-cadence
dispatcher land in subsequent Track G MVP commits.

The synthetic operator (Track H) invokes the reviewer explicitly after
the dev agent completes; this package does NOT yet wire into the
orchestrator's per-commit hook (separate G MVP follow-on).
"""
from __future__ import annotations

from jig.reviewers.comment import (
    BonesCommentType,
    ReviewerComment,
    ReviewerCommentType,
    Severity,
)
from jig.reviewers.contract_compliance import ContractComplianceReviewer
from jig.reviewers.cross_cutting_policy import CrossCuttingPolicyReviewer
from jig.reviewers.dispatch import (
    BONES_REVIEWER_ID,
    CROSS_CUTTING_REVIEWER_ID,
    INTENT_REVIEWER_ID,
    select_reviewers_for_ticket,
    should_run_for_bones,
)
from jig.reviewers.intent_compliance import (
    IntentCommentType,
    IntentComplianceReviewer,
    review_intent,
)

__all__ = [
    "BONES_REVIEWER_ID",
    "BonesCommentType",
    "CROSS_CUTTING_REVIEWER_ID",
    "ContractComplianceReviewer",
    "CrossCuttingPolicyReviewer",
    "INTENT_REVIEWER_ID",
    "IntentCommentType",
    "IntentComplianceReviewer",
    "ReviewerComment",
    "ReviewerCommentType",
    "Severity",
    "review_intent",
    "select_reviewers_for_ticket",
    "should_run_for_bones",
]
