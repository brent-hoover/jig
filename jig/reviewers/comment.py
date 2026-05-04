"""Structured reviewer comment — bones-scope subset of the v2 schema.

The full schema (per ``docs/pm-workflow/design.md`` §"Comment structure
(machine-first)") includes the mechanical types (``contract-violation``,
``cross-cutting-policy-violation``, ``spec-violation``) and the judgment
types (``pattern-divergence``, ``error-handling``, ``test-adequacy``,
``code-clarity``). Bones ships only the three types the contract-
compliance reviewer can produce; the union grows incrementally as
each new reviewer lands.

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


class BonesCommentType(str, Enum):
    """The subset of comment types the bones contract-compliance reviewer emits.

    Full taxonomy lands incrementally with the rest of the federation —
    see ``docs/pm-workflow/design.md`` §"Comment structure". Keeping
    this enum bones-scoped makes it impossible to forget to extend it
    when MVP reviewers ship.
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


class ReviewerComment(BaseModel):
    """One finding from one reviewer.

    All fields except ``type``, ``severity``, ``reviewer``, and
    ``prose`` are optional — different finding kinds populate different
    structural positions. ``confidence`` defaults to ``1.0`` because
    bones only ships mechanical reviewers; judgment reviewers (G6) will
    set it lower.
    """

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    type: BonesCommentType
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


__all__ = [
    "BonesCommentType",
    "ReviewerComment",
    "Severity",
]
