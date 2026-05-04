"""Reviewer self-check gate (Track G Final).

Per ``docs/v2.0/pm-workflow/design.md`` §"Reviewer self-check before
posting", every reviewer agent reviews its own output before
publishing. Final ships the deterministic mechanical gate that the
``reviewer_post_comment`` MCP tool runs before persistence; the
LLM-driven self-review is a v2.x layer.

These tests pin each rule of the gate plus the integration with
``handle_reviewer_post_comment`` so a future tweak to the rules has
to update both the rule + its test, surfacing intent.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jig.reviewer_mcp import SelfCheckDropped, handle_reviewer_post_comment
from jig.reviewers.comment import ReviewerComment, ReviewerCommentType, Severity
from jig.reviewers.self_check import (
    MIN_PROSE_LENGTH,
    NOTABLE_CONFIDENCE_THRESHOLD,
    POST_CONFIDENCE_FLOOR,
    SelfCheckResult,
    validate_comment_for_self_check,
)
from jig.store.review_comments import ReviewCommentsStore


# ---- helpers -------------------------------------------------------------


def _comment(
    *,
    severity: str = "important",
    confidence: float = 0.8,
    prose: str = "x" * (MIN_PROSE_LENGTH + 5),
    file: str | None = "jig/foo.py",
    contract_uri: str | None = None,
    type_: str = "pattern-divergence",
) -> ReviewerComment:
    return ReviewerComment(
        type=ReviewerCommentType(type_),
        severity=Severity(severity),
        reviewer="reviewer-pattern-conformance",
        prose=prose,
        confidence=confidence,
        file=file,
        contract_uri=contract_uri,
    )


# ---- happy pass-through --------------------------------------------------


class TestPassThrough:
    def test_default_comment_passes(self) -> None:
        result = validate_comment_for_self_check(_comment())
        assert result.should_post is True
        assert result.reason is None

    def test_anchored_via_contract_uri_passes(self) -> None:
        result = validate_comment_for_self_check(
            _comment(file=None, contract_uri="project://arch/contracts#x")
        )
        assert result.should_post is True

    def test_returns_self_check_result_pydantic_model(self) -> None:
        result = validate_comment_for_self_check(_comment())
        assert isinstance(result, SelfCheckResult)


# ---- rule 1: critical always passes -------------------------------------


class TestCriticalAlwaysPasses:
    def test_critical_with_low_confidence_passes(self) -> None:
        """Operator must see blockers regardless of judgment confidence."""
        result = validate_comment_for_self_check(
            _comment(severity="critical", confidence=0.1)
        )
        assert result.should_post is True

    def test_critical_with_short_prose_passes(self) -> None:
        result = validate_comment_for_self_check(
            _comment(severity="critical", prose="bad")
        )
        assert result.should_post is True

    def test_critical_with_no_anchor_passes(self) -> None:
        result = validate_comment_for_self_check(
            _comment(severity="critical", file=None, contract_uri=None)
        )
        assert result.should_post is True


# ---- rule 2: confidence floor --------------------------------------------


class TestConfidenceFloor:
    def test_below_floor_drops(self) -> None:
        result = validate_comment_for_self_check(
            _comment(confidence=POST_CONFIDENCE_FLOOR - 0.05)
        )
        assert result.should_post is False
        assert "confidence" in (result.reason or "").lower()

    def test_at_floor_passes(self) -> None:
        """Boundary: floor itself is the threshold; values < floor drop."""
        result = validate_comment_for_self_check(
            _comment(confidence=POST_CONFIDENCE_FLOOR)
        )
        # 0.4 fails the notable-threshold rule? Only if severity is
        # notable. With important severity, 0.4 passes the floor and
        # passes overall.
        assert result.should_post is True

    def test_just_above_floor_passes(self) -> None:
        result = validate_comment_for_self_check(
            _comment(confidence=POST_CONFIDENCE_FLOOR + 0.01)
        )
        assert result.should_post is True


# ---- rule 3: notable + uncertain = noise --------------------------------


class TestNotableConfidenceCombo:
    def test_notable_below_threshold_drops(self) -> None:
        result = validate_comment_for_self_check(
            _comment(
                severity="notable",
                confidence=NOTABLE_CONFIDENCE_THRESHOLD - 0.05,
            )
        )
        assert result.should_post is False
        assert "notable" in (result.reason or "").lower()

    def test_notable_at_threshold_passes(self) -> None:
        result = validate_comment_for_self_check(
            _comment(
                severity="notable",
                confidence=NOTABLE_CONFIDENCE_THRESHOLD,
            )
        )
        assert result.should_post is True

    def test_important_below_threshold_passes(self) -> None:
        """Threshold only applies to notable severity."""
        result = validate_comment_for_self_check(
            _comment(
                severity="important",
                confidence=NOTABLE_CONFIDENCE_THRESHOLD - 0.1,
            )
        )
        assert result.should_post is True


# ---- rule 4: prose too short --------------------------------------------


class TestProseTooShort:
    def test_short_prose_drops(self) -> None:
        result = validate_comment_for_self_check(
            _comment(prose="x" * (MIN_PROSE_LENGTH - 1))
        )
        assert result.should_post is False
        assert "boilerplate" in (result.reason or "").lower()

    def test_exact_min_passes(self) -> None:
        result = validate_comment_for_self_check(
            _comment(prose="x" * MIN_PROSE_LENGTH)
        )
        assert result.should_post is True


# ---- rule 5: no anchor --------------------------------------------------


class TestNoAnchor:
    def test_no_anchor_drops(self) -> None:
        result = validate_comment_for_self_check(
            _comment(file=None, contract_uri=None)
        )
        assert result.should_post is False
        assert "anchor" in (result.reason or "").lower()

    def test_anchor_via_file_passes(self) -> None:
        result = validate_comment_for_self_check(
            _comment(file="jig/foo.py", contract_uri=None)
        )
        assert result.should_post is True

    def test_anchor_via_contract_uri_passes(self) -> None:
        result = validate_comment_for_self_check(
            _comment(file=None, contract_uri="project://arch/x#y")
        )
        assert result.should_post is True


# ---- MCP integration -----------------------------------------------------


class TestMCPHandlerWiring:
    async def test_passing_comment_persists(self, tmp_path: Path) -> None:
        cid = await handle_reviewer_post_comment(
            project_path=tmp_path,
            reviewer_role="reviewer-pattern-conformance",
            args={
                "type": "pattern-divergence",
                "severity": "important",
                "prose": (
                    "Local helper duplicates code in jig/foo.py — extract "
                    "to a shared utility."
                ),
                "confidence": 0.8,
                "file": "jig/foo.py",
                "line": 12,
            },
            ticket_id="tkt-self",
            cycle=1,
        )
        assert cid

    async def test_dropped_comment_raises_self_check_dropped(
        self, tmp_path: Path
    ) -> None:
        with pytest.raises(SelfCheckDropped) as excinfo:
            await handle_reviewer_post_comment(
                project_path=tmp_path,
                reviewer_role="reviewer-pattern-conformance",
                args={
                    "type": "pattern-divergence",
                    "severity": "notable",
                    "prose": (
                        "Possibly worth thinking about idiom here later"
                    ),
                    "confidence": 0.3,  # below floor
                    "file": "jig/foo.py",
                },
            )
        assert excinfo.value.result.should_post is False
        # Subclasses ValueError so existing exception handling keeps working.
        assert isinstance(excinfo.value, ValueError)

    async def test_dropped_comment_does_not_persist(
        self, tmp_path: Path
    ) -> None:
        with pytest.raises(SelfCheckDropped):
            await handle_reviewer_post_comment(
                project_path=tmp_path,
                reviewer_role="reviewer-pattern-conformance",
                args={
                    "type": "pattern-divergence",
                    "severity": "notable",
                    "prose": "short",
                    "confidence": 0.95,
                    "file": "jig/foo.py",
                },
            )
        store = ReviewCommentsStore(
            tmp_path / ".jig" / "store" / "review_comments.jsonl"
        )
        await store.load()
        # Nothing persisted.
        assert not (await store.for_ticket("any"))

    async def test_critical_with_short_prose_still_persists(
        self, tmp_path: Path
    ) -> None:
        """Rule 1 trumps rule 4 for criticals — operator must see blockers."""
        cid = await handle_reviewer_post_comment(
            project_path=tmp_path,
            reviewer_role="reviewer-error-handling",
            args={
                "type": "error-handling",
                "severity": "critical",
                "prose": "secret leak",  # below MIN_PROSE_LENGTH
                "confidence": 0.95,
                "file": "jig/auth.py",
                "line": 4,
            },
            ticket_id="tkt-crit",
        )
        assert cid
