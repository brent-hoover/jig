"""Reviewer self-check gate before comment persistence (Track G Final).

Per ``docs/pm-workflow/design.md`` §"Reviewer self-check before
posting", every reviewer agent reviews its own output before publishing
comments — a cheap filter for false positives. Final ships the
**mechanical** gate that the reviewer's ``reviewer_post_comment`` MCP
tool runs before write; an LLM-driven self-review pass at agent scope
is a v2.x layer.

The gate is a pure function — no I/O, no LLM. It looks at the comment
shape (severity, confidence, prose length, anchor presence) and
decides whether to post or drop. Critical comments always pass through
because the operator must see them; the gate's job is to catch
low-signal noise from judgment reviewers.

Wired into ``jig.reviewer_mcp.handle_reviewer_post_comment`` so the
gate runs deterministically before any persistence happens. A dropped
comment returns a ``SelfCheckResult(should_post=False, reason=...)``;
the MCP handler raises so the reviewer agent's tool call gets a clear
"this got dropped" signal it can incorporate into its next decision.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from jig.reviewers.comment import ReviewerComment, Severity

__all__ = [
    "MIN_PROSE_LENGTH",
    "NOTABLE_CONFIDENCE_THRESHOLD",
    "POST_CONFIDENCE_FLOOR",
    "SelfCheckResult",
    "validate_comment_for_self_check",
]

# Drop comments below this confidence floor — judgment-reviewer noise
# at this level is more likely to mislead the dev agent than help.
# Mechanical reviewers report confidence=1.0 by definition so this
# threshold only meaningfully gates judgment reviewers.
POST_CONFIDENCE_FLOOR: float = 0.4

# Notable comments need higher confidence to escape the gate. The
# combination of low severity + low confidence is exactly the
# "advisory hunch" pattern that drowns the dev agent in noise.
NOTABLE_CONFIDENCE_THRESHOLD: float = 0.7

# Minimum prose length for a comment to be considered actionable.
# Below this is almost certainly boilerplate / placeholder text the
# reviewer agent forgot to fill in.
MIN_PROSE_LENGTH: int = 30


class SelfCheckResult(BaseModel):
    """Outcome of one self-check pass over a candidate comment.

    ``should_post`` is the gate's verdict; ``reason`` is human-readable
    prose surfaced to the reviewer agent (and the operator via
    analytics) when the gate drops a comment, so the failure is
    diagnosable without re-running the reviewer.
    """

    model_config = ConfigDict(extra="forbid")

    should_post: bool
    reason: str | None = Field(
        default=None,
        description=(
            "Set when ``should_post`` is False — short prose explaining "
            "which gate rule fired so the reviewer's caller can decide "
            "whether to refine and retry."
        ),
    )


def validate_comment_for_self_check(
    comment: ReviewerComment,
) -> SelfCheckResult:
    """Run the deterministic self-check rules against ``comment``.

    Returns ``SelfCheckResult(should_post=True)`` for comments that
    pass every gate; otherwise ``should_post=False`` with the
    specific rule that fired in ``reason``.

    Rules (in order, first-match wins):

    1. Critical severity always passes — operator must see blockers
       regardless of judgment-reviewer confidence.
    2. Confidence below ``POST_CONFIDENCE_FLOOR`` (0.4) → drop.
       Judgment-reviewer noise at this level is misleading.
    3. Notable severity AND confidence below
       ``NOTABLE_CONFIDENCE_THRESHOLD`` (0.7) → drop. The
       low-severity-plus-low-confidence cell of the matrix is exactly
       the advisory-hunch noise the gate exists to suppress.
    4. Prose shorter than ``MIN_PROSE_LENGTH`` (30 chars) → drop.
       Almost certainly placeholder text; the operator can't act on
       a 5-character comment.
    5. No anchor (empty ``file`` AND empty ``contract_uri``) → drop.
       Without a path or contract URI the operator has nothing to
       click into; the comment is unactionable.

    All rules are independent — adding / reordering them happens here
    rather than in the MCP handler so callers can also run the gate
    directly (e.g. an analytics audit pass over historic comments).
    """
    # Rule 1 — critical always passes. Operator must see blockers.
    if comment.severity == Severity.CRITICAL.value:
        return SelfCheckResult(should_post=True)

    # Rule 2 — low-confidence floor.
    if comment.confidence < POST_CONFIDENCE_FLOOR:
        return SelfCheckResult(
            should_post=False,
            reason=(
                f"confidence {comment.confidence:.2f} below floor "
                f"{POST_CONFIDENCE_FLOOR:.2f}; comment likely noise"
            ),
        )

    # Rule 3 — notable + uncertain = noise.
    if (
        comment.severity == Severity.NOTABLE.value
        and comment.confidence < NOTABLE_CONFIDENCE_THRESHOLD
    ):
        return SelfCheckResult(
            should_post=False,
            reason=(
                f"notable severity with confidence {comment.confidence:.2f} "
                f"below notable threshold "
                f"{NOTABLE_CONFIDENCE_THRESHOLD:.2f}"
            ),
        )

    # Rule 4 — prose too short.
    if len(comment.prose) < MIN_PROSE_LENGTH:
        return SelfCheckResult(
            should_post=False,
            reason=(
                f"prose length {len(comment.prose)} below minimum "
                f"{MIN_PROSE_LENGTH} chars; likely boilerplate"
            ),
        )

    # Rule 5 — no anchor (file + contract_uri both empty).
    if not comment.file and not comment.contract_uri:
        return SelfCheckResult(
            should_post=False,
            reason=(
                "comment has no anchor (file and contract_uri both empty); "
                "operator cannot act on it"
            ),
        )

    return SelfCheckResult(should_post=True)
