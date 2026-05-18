"""Reviewer federation — Track G of the v2 implementation plan.

The full design splits review into eight federated agents (contract
compliance, cross-cutting policy, spec compliance, pattern conformance,
error handling, test adequacy, security, performance, architectural,
visual compliance). See ``docs/v2.0/pm-workflow/design.md`` §"Reviewer
federation — selection logic" for the full taxonomy.

Currently shipped (mechanical, deterministic, no LLM):

- ``ContractComplianceReviewer`` — bones contract-compliance (Track G2).
- ``IntentComplianceReviewer`` — intent-layer enforcement (Track I MVP).
- ``CrossCuttingPolicyReviewer`` — universal-rule enforcement (Track G MVP).
- ``SpecComplianceReviewer`` — behavior-AC reference checks (Track G MVP).

Two-cadence dispatch (Track G MVP) is wired through
``dispatch_for_cadence``: per-commit cadence runs only the mechanical
reviewers (single-digit-second budget); end-of-ticket runs the full
default-on set per ``select_reviewers_for_ticket``. Per-commit
critical comments emit ``PerCommitCheckFailed`` analytics events when
the dispatch is wired through ``EventEmitter`` (separate orchestrator
follow-on).

The synthetic operator (Track H) invokes the reviewer explicitly after
the dev agent completes; this package does NOT yet wire into the
orchestrator's per-commit hook (separate G MVP follow-on).
"""

from __future__ import annotations

from jig.reviewers.comment import (
    BonesCommentType,
    Evidence,
    ReviewerComment,
    ReviewerCommentType,
    Severity,
    format_comment_markdown,
)
from jig.reviewers.contract_compliance import ContractComplianceReviewer
from jig.reviewers.cross_cutting_policy import CrossCuttingPolicyReviewer
from jig.reviewers.dispatch import (
    ARCHITECTURAL_REVIEWER_ID,
    BONES_REVIEWER_ID,
    CROSS_CUTTING_REVIEWER_ID,
    ERROR_HANDLING_REVIEWER_ID,
    INTENT_REVIEWER_ID,
    LlmReviewerPending,
    PATTERN_CONFORMANCE_REVIEWER_ID,
    PERFORMANCE_REVIEWER_ID,
    SECURITY_REVIEWER_ID,
    SPEC_COMPLIANCE_REVIEWER_ID,
    TEST_ADEQUACY_REVIEWER_ID,
    dispatch_for_cadence,
    dispatch_with_llm_spawn,
    select_reviewers_for_ticket,
    should_run_for_bones,
)
from jig.reviewers.intent_compliance import (
    IntentCommentType,
    IntentComplianceReviewer,
    review_intent,
)
from jig.reviewers.spec_compliance import SpecComplianceReviewer
from jig.reviewers.visual_compliance import (
    VISUAL_COMPLIANCE_REVIEWER_ID,
    VisualComplianceReviewer,
)

__all__ = [
    "ARCHITECTURAL_REVIEWER_ID",
    "BONES_REVIEWER_ID",
    "BonesCommentType",
    "CROSS_CUTTING_REVIEWER_ID",
    "ContractComplianceReviewer",
    "CrossCuttingPolicyReviewer",
    "ERROR_HANDLING_REVIEWER_ID",
    "Evidence",
    "INTENT_REVIEWER_ID",
    "IntentCommentType",
    "IntentComplianceReviewer",
    "LlmReviewerPending",
    "PATTERN_CONFORMANCE_REVIEWER_ID",
    "PERFORMANCE_REVIEWER_ID",
    "ReviewerComment",
    "ReviewerCommentType",
    "SECURITY_REVIEWER_ID",
    "SPEC_COMPLIANCE_REVIEWER_ID",
    "Severity",
    "SpecComplianceReviewer",
    "TEST_ADEQUACY_REVIEWER_ID",
    "VISUAL_COMPLIANCE_REVIEWER_ID",
    "VisualComplianceReviewer",
    "dispatch_for_cadence",
    "dispatch_with_llm_spawn",
    "format_comment_markdown",
    "review_intent",
    "select_reviewers_for_ticket",
    "should_run_for_bones",
]
