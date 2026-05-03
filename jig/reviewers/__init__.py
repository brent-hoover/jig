"""Reviewer federation — Track G of the v2 implementation plan.

The full design splits review into eight federated agents (contract
compliance, cross-cutting policy, spec compliance, pattern conformance,
error handling, test adequacy, security, performance, architectural,
visual compliance). See ``docs/pm-workflow/design.md`` §"Reviewer
federation — selection logic" for the full taxonomy.

Bones scope (this package, this commit) ships **only** Track G2 — a
mechanical, end-of-ticket **contract-compliance** reviewer. No LLM, no
per-commit cadence, no lead-reviewer dedup, no auto-apply. Per the
design: mechanical reviewers are deterministic checks with confidence
1.0 and are the only reviewers that can run in the per-commit latency
budget; this module ships the deterministic core that the per-commit
cadence (G3) and the rest of the federation (G6/G7) compose with later.

Public surface for bones:

- ``ReviewerComment`` — structured comment shape per design
  §"Comment structure (machine-first)".
- ``ContractComplianceReviewer.review`` — the bones reviewer.
- ``should_run_for_bones`` — bones dispatch helper. Defaults
  contract-compliance ON for any ``layer == "bones"`` ticket whose
  ``reviewer_set`` is empty (per Track F's handoff note).

The synthetic operator (Track H) invokes the reviewer explicitly after
the dev agent completes; this package does NOT wire into the
orchestrator's per-ticket lifecycle. That integration is an MVP
concern (Track G3 — two-cadence integration).
"""
from __future__ import annotations

from jig.reviewers.bones_dispatch import should_run_for_bones
from jig.reviewers.comment import (
    BonesCommentType,
    ReviewerComment,
    Severity,
)
from jig.reviewers.contract_compliance import ContractComplianceReviewer

__all__ = [
    "BonesCommentType",
    "ContractComplianceReviewer",
    "ReviewerComment",
    "Severity",
    "should_run_for_bones",
]
