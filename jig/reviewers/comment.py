"""Structured reviewer comment — federation-wide schema.

The full schema (per ``docs/pm-workflow/design.md`` §"Comment structure
(machine-first)") includes the mechanical types (``contract-violation``,
``cross-cutting-policy-violation``, ``spec-violation``) and the judgment
types (``pattern-divergence``, ``error-handling``, ``test-adequacy``,
``code-clarity``). The enum grows as each new reviewer lands; entries
that have shipped are listed below.

For bones we model the comment as a Pydantic v2 ``BaseModel`` with
``confidence`` defaulting to ``1.0`` (mechanical reviewers always
report 1.0 by definition) and the structural-position fields
(``contract_uri``, ``file``, ``line``, ``suggested_diff``) all
optional — the bones reviewer's checks don't pinpoint a single line
in every case (an empty diff has no file, an integration-AC reference
miss spans many).

This shape deliberately diverges from ``jig.check_results.CheckResult``
even though both record "something the harness checked". CheckResult is
verdict-shaped (pass/fail/timeout/error per *check definition*);
ReviewerComment is finding-shaped (one entry per concrete violation).
A single reviewer run can return zero, one, or many comments — the
mapping isn't 1:1 with check definitions.
"""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Severity(str, Enum):
    """Three-tier severity per design §"Severity tiers and disposition".

    Bones returns ``CRITICAL`` for empty-diff and missing-file cases
    (the dev agent didn't produce anything to review), ``IMPORTANT``
    for missing integration-AC references (the diff exists but doesn't
    demonstrably cover the AC), and ``NOTABLE`` for environmental
    issues like a missing contracts file (worth flagging, not blocking
    the ticket — bones tickets can legitimately precede SA contract
    authoring on a trivial scenario).
    """

    CRITICAL = "critical"
    IMPORTANT = "important"
    NOTABLE = "notable"


class ReviewerCommentType(str, Enum):
    """The subset of comment types currently emitted across the federation.

    Full taxonomy lands incrementally — see
    ``docs/pm-workflow/design.md`` §"Comment structure". Adding a new
    reviewer means adding its comment-type enum entries here so the
    union stays exhaustive and ``extra="forbid"`` validation catches
    typos in operator-authored fixtures.

    Currently shipped:

    * Mechanical contract-compliance (Track G2 bones) — empty-diff,
      integration-ac-not-referenced, contract-violation.
    * Mechanical intent-compliance (Track I MVP) — three intent-layer
      finding kinds.
    * Mechanical cross-cutting-policy (Track G MVP) — universal-rule
      violation + missing-positive-policy reference.
    * Mechanical spec-compliance (Track G MVP) — behavior-AC reference
      miss + ticket cites a capability that doesn't exist in the spec.
    * Judgment reviewers (Track G MVP follow-on) — pattern-divergence,
      error-handling, test-adequacy. LLM-driven via the
      ``reviewer_post_comment`` MCP tool; ``code-clarity`` reserved for
      a later judgment reviewer not in this MVP.
    """

    EMPTY_DIFF = "empty-diff"
    INTEGRATION_AC_NOT_REFERENCED = "integration-ac-not-referenced"
    CONTRACT_VIOLATION = "contract-violation"
    # Intent-layer enforcement (Track I MVP). The intent reviewer flags
    # thin/boilerplate Intent blocks on authored artifacts (Module,
    # DataContract, BehavioralContract, Risk, Epic). Mechanical, no LLM.
    INTENT_TOO_SHORT = "intent-too-short"
    INTENT_BOILERPLATE_RESTATEMENT = "intent-boilerplate-restatement"
    INTENT_COMPLICATIONS_SKIPPED = "intent-complications-skipped"
    # Cross-cutting policy (Track G MVP). Universal rules from
    # ``Architecture.cross_cutting_policies``.
    CROSS_CUTTING_POLICY_VIOLATION = "cross-cutting-policy-violation"
    CROSS_CUTTING_POLICY_NOT_REFERENCED = "cross-cutting-policy-not-referenced"
    # Spec-compliance (Track G MVP). Behavior-AC token reference checks
    # against the suite's structured spec, plus capability-id sanity.
    BEHAVIOR_AC_NOT_REFERENCED = "behavior-ac-not-referenced"
    CAPABILITY_NOT_FOUND_IN_SPEC = "capability-not-found-in-spec"
    # Judgment reviewers (Track G MVP follow-on). One per role; the
    # comment-type maps 1:1 to the reviewer that produces it. Confidence
    # < 1.0 by convention.
    PATTERN_DIVERGENCE = "pattern-divergence"
    ERROR_HANDLING = "error-handling"
    TEST_ADEQUACY = "test-adequacy"
    # Visual compliance (Track D MVP). Mechanical, no vision: verify
    # wireframe presence + linter pass + diff cites the screen id.
    # Vision-based screenshot diff is Final scope.
    WIREFRAME_NOT_FOUND = "wireframe-not-found"
    WIREFRAME_LINT_FAILED = "wireframe-lint-failed"
    WIREFRAME_NOT_REFERENCED = "wireframe-not-referenced"


# Backward-compatible alias. The bones-era name keeps working for the
# small handful of external callers that imported it; new code should
# use ``ReviewerCommentType``. Removed in a future cleanup once all
# call sites have migrated.
BonesCommentType = ReviewerCommentType


class ReviewerComment(BaseModel):
    """One finding from one reviewer.

    All fields except ``type``, ``severity``, ``reviewer``, and
    ``prose`` are optional — different finding kinds populate different
    structural positions. ``confidence`` defaults to ``1.0`` because
    bones only ships mechanical reviewers; judgment reviewers (G6) will
    set it lower.
    """

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    type: ReviewerCommentType
    severity: Severity
    reviewer: str = Field(
        ...,
        min_length=1,
        description=(
            "Reviewer id that produced this comment — e.g. "
            "'contract-compliance'. Used by the synthetic operator and "
            "(MVP) the lead-reviewer agent for dedup."
        ),
    )
    prose: str = Field(
        ...,
        min_length=1,
        description=(
            "Human-readable explanation. WHY the comment was raised, not "
            "just WHAT was wrong — see CLAUDE.md conventions."
        ),
    )
    contract_uri: str | None = Field(
        default=None,
        description=(
            "URI into the contracts artifact that this comment references; "
            "e.g. project://arch/modules/catalog-ingest/contracts#integration_ac/shopify-connect. "
            "Bones populates this for AC-reference misses; MVP populates it "
            "for ownership/schema/API violations."
        ),
    )
    file: str | None = Field(
        default=None,
        description="Repo-relative path of the offending file; None when comment is diff-wide.",
    )
    line: int | None = Field(
        default=None,
        ge=1,
        description="Line number in ``file`` when the finding pinpoints one.",
    )
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description=(
            "Mechanical reviewers report 1.0 by definition (design "
            '§"Comment structure"); judgment reviewers report lower.'
        ),
    )
    suggested_diff: str | None = Field(
        default=None,
        description=(
            "Optional fix-it patch. Bones doesn't propose diffs (the "
            "checks are too coarse); the auto-apply path (G4) consumes "
            "this in MVP."
        ),
    )
    cadence: Literal["per_commit", "end_of_ticket"] = Field(
        default="end_of_ticket",
        description=(
            'Two-cadence review per design §"Two-cadence review". '
            "Per-commit cadence runs only the mechanical reviewers "
            "within seconds of git commit; end-of-ticket runs the full "
            "federation. Defaults to end_of_ticket so bones-era callers "
            "(which only ever ran end-of-ticket) keep working without "
            "passing the field explicitly."
        ),
    )
    ticket_id: str | None = Field(
        default=None,
        description=(
            "Ticket the comment was raised against. Optional because "
            "bones-era reviewers were called from contexts that didn't "
            "always have a ticket id in scope (the synthetic-operator "
            "harness invoked them by URI). MVP callers populate it; the "
            "ReviewCommentsStore indexes on this field for "
            "``for_ticket`` queries."
        ),
    )
    cycle: int = Field(
        default=0,
        ge=0,
        description=(
            "Review→fix cycle number this comment belongs to. Starts at "
            "0 for the first review pass, increments each time the dev "
            "agent re-submits and the reviewers re-run. The bounded "
            "fix-loop cap (3 cycles per design §\"Bounded fix loops\") "
            "compares categories across consecutive cycles to detect "
            "non-converging issues."
        ),
    )


__all__ = [
    "BonesCommentType",
    "ReviewerComment",
    "ReviewerCommentType",
    "Severity",
]
