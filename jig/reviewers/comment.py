"""Structured reviewer comment — federation-wide schema.

The full schema (per ``docs/v2.0/pm-workflow/design.md`` §"Comment structure
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

Track G Final adds two optional polish fields:

- ``evidence`` — list of ``Evidence`` records pointing at the source
  material that supports the finding (a diff hunk, a test output, a
  spec passage). Lets the operator click through to the actual
  artifact instead of trusting the prose.
- ``auto_apply_after`` — when populated, ``suggested_diff`` is queued
  for auto-apply after N seconds, giving the operator a window to
  override before the orchestrator commits. Default ``None``
  preserves the bones-era behavior.

Plus ``format_comment_markdown`` — a pure rendering helper used by the
CLI and TUI to surface a comment in human-readable form.

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

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    ``docs/v2.0/pm-workflow/design.md`` §"Comment structure". Adding a new
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
    # Intent-layer enforcement Final scope (Track I Final). Two
    # additional check kinds: ``intent-no-citations`` flags
    # complications_considered prose that's all four short and
    # contains no concrete file/identifier/ticket references;
    # ``intent-duplicate-across-artifacts`` flags two artifacts
    # carrying identical (problem, simplest_solution) prose pairs
    # (the agent copy-pasted boilerplate across artifacts).
    INTENT_NO_CITATIONS = "intent-no-citations"
    INTENT_DUPLICATE_ACROSS_ARTIFACTS = "intent-duplicate-across-artifacts"
    # Cross-cutting policy (Track G MVP). Universal rules from
    # ``Architecture.cross_cutting_policies``.
    CROSS_CUTTING_POLICY_VIOLATION = "cross-cutting-policy-violation"
    CROSS_CUTTING_POLICY_NOT_REFERENCED = "cross-cutting-policy-not-referenced"
    # Spec-compliance (Track G MVP). Behavior-AC token reference checks
    # against the suite's structured spec, plus capability-id sanity.
    BEHAVIOR_AC_NOT_REFERENCED = "behavior-ac-not-referenced"
    CAPABILITY_NOT_FOUND_IN_SPEC = "capability-not-found-in-spec"
    # Tradeoff compliance (Phase 1). Fired when a ticket re-adds work
    # that was explicitly deferred to a later layer in the tradeoff ledger.
    DEFERRED_WORK_REINTRODUCED = "deferred-work-reintroduced"
    # Contract test coverage (Phase 3). Fired when a module consumes an API
    # or event from another module but neither side's integration_ac mentions
    # the contract name — makes the gap visible so the SA can add a MUST entry.
    CONTRACT_TEST_COVERAGE_GAP = "contract-test-coverage-gap"
    # Tracer preservation (Phase 5). Fired when a ticket touches a node
    # covered by a tracer — the tracer should be re-run to confirm the
    # smoke still passes after the change.
    TRACER_PRESERVATION_RISK = "tracer-preservation-risk"
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
    # Visual compliance (Track D Final). Vision-based screenshot diff
    # surfaced as one comment per spotted ``VisualDifference``;
    # accessibility (WCAG AA) violations and responsive-design
    # violations land alongside as their own comment kinds. The
    # vision-diff path takes its severity from the vision provider;
    # the a11y / responsive paths set severity deterministically.
    SCREENSHOT_MISSING = "screenshot-missing"
    VISUAL_VISION_DIFF = "visual-vision-diff"
    ACCESSIBILITY_VIOLATION = "accessibility-violation"
    RESPONSIVE_DESIGN_VIOLATION = "responsive-design-violation"


# Backward-compatible alias. The bones-era name keeps working for the
# small handful of external callers that imported it; new code should
# use ``ReviewerCommentType``. Removed in a future cleanup once all
# call sites have migrated.
BonesCommentType = ReviewerCommentType


# Comment types that legitimately can't carry a ``file`` / ``line``
# anchor because they describe the worktree as a whole rather than
# a specific source location. The mechanical-critical anchor invariant
# in ``ReviewerComment._enforce_type_field_constraints`` skips these.
_WORKTREE_LEVEL_TYPES: frozenset[str] = frozenset({"empty-diff"})


class Evidence(BaseModel):
    """One piece of supporting evidence for a reviewer comment.

    Track G Final addition. Lets reviewers cite the source material
    that backs a finding (diff hunk, test run output, static analysis
    pointer, spec text excerpt) so the operator can click through
    rather than re-derive context from the prose alone.

    ``source`` constrains the small set of evidence kinds the
    federation produces today — the union grows as new reviewer
    integrations land. ``reference`` is a URI when one exists
    (``project://...``) or a file/path string when not. ``excerpt``
    is optional inline content (a few lines of code, a test output
    snippet) — the operator-facing renderer truncates long excerpts
    to keep the comment scannable.
    """

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    source: Literal[
        "diff",
        "test_run",
        "static_analysis",
        "spec_text",
    ]
    reference: str = Field(
        ...,
        min_length=1,
        description=(
            "URI or path pointing at the evidence — e.g. a "
            "project:// URI for a contract, or a repo-relative path "
            "for a code line / test fixture."
        ),
    )
    excerpt: str | None = Field(
        default=None,
        description=(
            "Optional inline content — a code snippet, test output "
            "fragment, or spec quote. Long excerpts get truncated by "
            "the operator-facing renderer."
        ),
    )


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
    # Track G Final polish — see module docstring.
    evidence: list[Evidence] = Field(
        default_factory=list,
        description=(
            "Supporting evidence for the finding. Empty by default so "
            "existing callers keep working; reviewers populate when "
            "they have a clickable artifact to surface."
        ),
    )
    auto_apply_after: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Optional auto-apply window in seconds. When populated, "
            "the orchestrator queues the suggested_diff for application "
            "after this many seconds, giving the operator a window to "
            "override. ``None`` means no auto-apply scheduled (the "
            "bones-era behavior). Has no effect when suggested_diff "
            "is empty."
        ),
    )
    # Track D Final — accessibility-violation comments cite the
    # specific WCAG rule that fired (e.g. "WCAG 1.1.1 Non-text Content")
    # so the operator can map straight back to the spec. Optional
    # because non-a11y comment kinds don't carry one.
    wcag_rule_id: str | None = Field(
        default=None,
        description=(
            "WCAG rule identifier the comment cites (e.g. 'WCAG 1.1.1 "
            "Non-text Content'). Populated only by the accessibility "
            "reviewer; other reviewers leave it ``None``."
        ),
    )
    # Track D Final — responsive-design-violation comments name the
    # breakpoint that failed (mobile / tablet / desktop) so the
    # operator knows where to look. ``None`` for non-responsive
    # comments and for responsive comments that span every breakpoint
    # (e.g. missing viewport meta).
    breakpoint: str | None = Field(
        default=None,
        description=(
            "Breakpoint label the responsive-design reviewer flagged "
            "(typically 'mobile' / 'tablet' / 'desktop'). ``None`` for "
            "non-responsive comments or for findings that apply to "
            "every breakpoint."
        ),
    )

    @model_validator(mode="after")
    def _enforce_type_field_constraints(self) -> ReviewerComment:
        """Pin the type → required-fields invariants the design carries
        in prose at the schema layer (type-design Block B).

        ``ReviewerComment`` stays a single envelope rather than a true
        discriminated union of subtypes — the existing callers and
        store layer build it with kwargs, and a true ADT split touches
        every reviewer. The cheap validator path catches the same
        misuses (an accessibility comment with no WCAG rule id,
        an auto-apply window with no diff to apply) at construction
        time without forcing the refactor.
        """
        # Read ``type`` as its raw string value because
        # ``use_enum_values=True`` already coerced the enum down to
        # its str form when the model finished construction.
        type_value = (
            self.type.value
            if isinstance(self.type, ReviewerCommentType)
            else str(self.type)
        )
        if (
            type_value == ReviewerCommentType.ACCESSIBILITY_VIOLATION.value
            and self.wcag_rule_id is None
        ):
            raise ValueError(
                "ReviewerComment.wcag_rule_id is required when "
                "type='accessibility-violation'. The comment cites a "
                "specific WCAG rule; the operator needs the rule id "
                "to map back to the spec."
            )
        # Note: ``breakpoint`` is intentionally allowed to be ``None``
        # for responsive-design comments that apply across every
        # breakpoint (per the field docstring) — viewport-meta and
        # similar "single root cause, every device" findings shouldn't
        # be forced to pick a label.
        # ``contract-violation`` is also used for meta-findings the
        # contract reviewer surfaces when it can't run (e.g. a ticket
        # with no module_id, or a module with no contracts.yaml). Those
        # carry ``severity == notable`` and don't have a specific
        # contract URI to cite. Only require the URI on actionable
        # critical/important findings.
        severity_value_for_contract = (
            self.severity.value
            if isinstance(self.severity, Severity)
            else str(self.severity)
        )
        if (
            type_value == ReviewerCommentType.CONTRACT_VIOLATION.value
            and self.contract_uri is None
            and severity_value_for_contract
            in {Severity.CRITICAL.value, Severity.IMPORTANT.value}
        ):
            raise ValueError(
                "ReviewerComment.contract_uri is required when "
                "type='contract-violation' and severity is "
                "critical/important. The comment names a specific "
                "contract URI; without it the violation has no anchor."
            )
        if self.auto_apply_after is not None and self.suggested_diff is None:
            raise ValueError(
                "ReviewerComment.auto_apply_after requires "
                "suggested_diff. Auto-applying nothing is nonsense; "
                "the orchestrator needs a diff to commit."
            )
        # Mechanical critical (confidence==1.0, severity=='critical'):
        # the operator triages by jumping to the file:line. Without
        # an anchor the comment is nearly unactionable.
        #
        # Worktree-level finding types are exempt: ``empty-diff`` IS the
        # comment that there's no code to anchor to, and forcing a
        # synthetic file/line on it would only obscure the reason.
        severity_value = (
            self.severity.value
            if isinstance(self.severity, Severity)
            else str(self.severity)
        )
        if (
            type_value not in _WORKTREE_LEVEL_TYPES
            and self.confidence == 1.0
            and severity_value == Severity.CRITICAL.value
            and self.file is None
            and self.contract_uri is None
        ):
            raise ValueError(
                "ReviewerComment with confidence=1.0 and "
                "severity='critical' must set file or contract_uri. "
                "Mechanical critical findings without an anchor are "
                "nearly unactionable for the operator."
            )
        return self


# ---- markdown rendering --------------------------------------------------


_MAX_EXCERPT_LINES: int = 8
_MAX_DIFF_LINES: int = 20


def format_comment_markdown(comment: ReviewerComment) -> str:
    """Render ``comment`` as operator-facing markdown.

    Pure function — no I/O, no side effects. Used by both the CLI
    (``jig story`` and friends) and the TUI to surface comments
    consistently. Truncates long excerpts / diffs so a single
    comment stays scannable; the operator can pull the full payload
    from the JSONL store if needed.

    Output shape::

        ### [SEVERITY] reviewer-id — type
        anchor: file:line OR contract_uri
        confidence: 0.85   cadence: end_of_ticket   cycle: 0
        > prose...

        suggested diff (truncated to N lines):
        ```diff
        ...
        ```

        evidence:
        - [diff] reference (excerpt...)
    """
    lines: list[str] = []

    # Header line: severity + reviewer + type.
    severity = (
        comment.severity.upper()
        if isinstance(comment.severity, str)
        else str(comment.severity).upper()
    )
    lines.append(
        f"### [{severity}] {comment.reviewer} — {comment.type}"
    )

    # Anchor line.
    anchor = _format_anchor(comment)
    if anchor:
        lines.append(f"anchor: {anchor}")

    # Metadata line.
    meta_parts = [
        f"confidence: {comment.confidence:.2f}",
        f"cadence: {comment.cadence}",
        f"cycle: {comment.cycle}",
    ]
    if comment.auto_apply_after is not None:
        meta_parts.append(
            f"auto-apply in: {comment.auto_apply_after}s"
        )
    lines.append("   ".join(meta_parts))
    lines.append("")

    # Prose as a blockquote so it's visually distinct from metadata.
    for prose_line in comment.prose.splitlines() or [comment.prose]:
        lines.append(f"> {prose_line}")
    lines.append("")

    # Suggested diff, truncated.
    if comment.suggested_diff:
        diff_lines = comment.suggested_diff.splitlines()
        truncated = diff_lines[:_MAX_DIFF_LINES]
        if len(diff_lines) > _MAX_DIFF_LINES:
            note = (
                f"suggested diff "
                f"(truncated to {_MAX_DIFF_LINES} of "
                f"{len(diff_lines)} lines):"
            )
        else:
            note = "suggested diff:"
        lines.append(note)
        lines.append("```diff")
        lines.extend(truncated)
        lines.append("```")
        lines.append("")

    # Evidence list.
    if comment.evidence:
        lines.append("evidence:")
        for ev in comment.evidence:
            lines.append(_format_evidence_line(ev))
        lines.append("")

    # Drop the trailing blank line — markdown renderers re-add spacing.
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def _format_anchor(comment: ReviewerComment) -> str:
    """Build the anchor string ``file:line`` / ``contract_uri`` / ''.

    Prefers ``file:line`` when both file and line are set; falls back
    to bare file or to the contract URI when no file is present.
    Empty string when the comment has no anchor at all (the
    self-check gate normally drops these, but format remains a pure
    function and must handle them).
    """
    if comment.file is not None:
        if comment.line is not None:
            return f"{comment.file}:{comment.line}"
        return comment.file
    if comment.contract_uri is not None:
        return comment.contract_uri
    return ""


def _format_evidence_line(ev: Evidence) -> str:
    """Render one evidence entry as a single bullet.

    Truncates the excerpt to ``_MAX_EXCERPT_LINES`` lines so a noisy
    test output doesn't blow up the comment view. Lossy truncation
    is fine here — the operator can pull the full evidence from the
    underlying artifact via ``reference``.
    """
    source = ev.source if isinstance(ev.source, str) else str(ev.source)
    base = f"- [{source}] {ev.reference}"
    if not ev.excerpt:
        return base
    excerpt_lines = ev.excerpt.splitlines()
    truncated = excerpt_lines[:_MAX_EXCERPT_LINES]
    if len(excerpt_lines) > _MAX_EXCERPT_LINES:
        truncated.append(
            f"... ({len(excerpt_lines) - _MAX_EXCERPT_LINES} more lines)"
        )
    excerpt = " ".join(line.strip() for line in truncated if line.strip())
    if len(excerpt) > 160:
        excerpt = excerpt[:160].rstrip() + "..."
    if excerpt:
        return f"{base} — {excerpt}"
    return base


__all__ = [
    "BonesCommentType",
    "Evidence",
    "ReviewerComment",
    "ReviewerCommentType",
    "Severity",
    "format_comment_markdown",
]
