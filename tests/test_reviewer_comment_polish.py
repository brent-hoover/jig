"""Structured-comment + suggested-diff polish (Track G Final).

Per ``docs/v2.0/pm-workflow/design.md`` §"Comment structure (machine-first)":

- Optional ``evidence: list[Evidence]`` field on ``ReviewerComment``.
- Optional ``auto_apply_after: int | None`` field.
- ``format_comment_markdown(comment) -> str`` — pure rendering helper.

These tests pin (a) round-trip for the new fields, (b) markdown
rendering for the common shapes, (c) evidence aggregation across a
list of comments. The bones-era field defaults stay backward-
compatible so existing JSONL records keep loading without migration.
"""
from __future__ import annotations

import pytest

from jig.reviewers import (
    Evidence,
    ReviewerComment,
    ReviewerCommentType,
    Severity,
    format_comment_markdown,
)


def _comment(
    *,
    severity: str = "important",
    confidence: float = 0.85,
    prose: str = "Local helper duplicates code in jig/foo.py.",
    file: str | None = "jig/foo.py",
    line: int | None = 12,
    contract_uri: str | None = None,
    suggested_diff: str | None = None,
    evidence: list[Evidence] | None = None,
    auto_apply_after: int | None = None,
    type_: str = "pattern-divergence",
    reviewer: str = "reviewer-pattern-conformance",
) -> ReviewerComment:
    return ReviewerComment(
        type=ReviewerCommentType(type_),
        severity=Severity(severity),
        reviewer=reviewer,
        prose=prose,
        confidence=confidence,
        file=file,
        line=line,
        contract_uri=contract_uri,
        suggested_diff=suggested_diff,
        evidence=evidence or [],
        auto_apply_after=auto_apply_after,
    )


# ---- Evidence model -----------------------------------------------------


class TestEvidenceModel:
    def test_minimal_evidence_constructs(self) -> None:
        ev = Evidence(source="diff", reference="jig/foo.py:12")
        assert ev.source == "diff"
        assert ev.reference == "jig/foo.py:12"
        assert ev.excerpt is None

    def test_excerpt_optional(self) -> None:
        ev = Evidence(
            source="test_run",
            reference="tests/test_x.py::test_foo",
            excerpt="AssertionError: expected 1 got 2",
        )
        assert ev.excerpt is not None

    def test_unknown_source_rejected(self) -> None:
        with pytest.raises(Exception):
            Evidence(source="random_source", reference="x")  # type: ignore[arg-type]

    def test_empty_reference_rejected(self) -> None:
        with pytest.raises(Exception):
            Evidence(source="diff", reference="")


# ---- ReviewerComment field round-trip ------------------------------------


class TestRoundTrip:
    def test_evidence_default_empty(self) -> None:
        c = _comment()
        assert c.evidence == []

    def test_auto_apply_after_default_none(self) -> None:
        c = _comment()
        assert c.auto_apply_after is None

    def test_round_trip_with_evidence_and_auto_apply(self) -> None:
        c = _comment(
            evidence=[
                Evidence(
                    source="diff",
                    reference="jig/foo.py:12",
                    excerpt="duplicated helper",
                ),
                Evidence(
                    source="spec_text",
                    reference="project://arch/contracts#x",
                ),
            ],
            suggested_diff="--- a/foo\n+++ b/foo\n@@\n-x\n+y\n",
            auto_apply_after=120,
        )
        payload = c.model_dump(mode="json")
        restored = ReviewerComment.model_validate(payload)
        assert len(restored.evidence) == 2
        assert restored.evidence[0].source == "diff"
        assert restored.evidence[1].source == "spec_text"
        assert restored.auto_apply_after == 120

    def test_negative_auto_apply_rejected(self) -> None:
        with pytest.raises(Exception):
            _comment(auto_apply_after=-1)

    def test_existing_records_load_without_new_fields(self) -> None:
        """A JSONL record from before Track G Final must still load."""
        legacy_payload = {
            "type": "pattern-divergence",
            "severity": "important",
            "reviewer": "reviewer-pattern-conformance",
            "prose": "x",
            "confidence": 0.8,
        }
        c = ReviewerComment.model_validate(legacy_payload)
        assert c.evidence == []
        assert c.auto_apply_after is None


# ---- target_role (review-routing) ----------------------------------------


class TestTargetRole:
    """``target_role`` lets a reviewer route a finding above the writing
    role's pay grade — e.g. flag a finding as belonging to ``pm`` or
    ``sa`` when the issue is with the spec / architecture rather than
    the code itself. The fix-loop router consults this field before
    falling back to file-glob routing.
    """

    def test_target_role_defaults_to_none(self) -> None:
        c = _comment()
        assert c.target_role is None

    def test_round_trip_with_target_role_set(self) -> None:
        """Build via ``model_validate`` so field validation runs at
        construction time, not just on the round-trip restore. (Pydantic's
        ``model_copy(update=...)`` writes the value directly without
        re-running validators.)"""
        payload = {
            "type": "pattern-divergence",
            "severity": "important",
            "reviewer": "reviewer-pattern-conformance",
            "prose": "spec is wrong — needs PM input",
            "confidence": 0.8,
            "target_role": "sa",
        }
        c = ReviewerComment.model_validate(payload)
        assert c.target_role == "sa"
        # Round-trip preserves the field.
        restored = ReviewerComment.model_validate(c.model_dump(mode="json"))
        assert restored.target_role == "sa"

    def test_target_role_accepts_arbitrary_role_string(self) -> None:
        """Validation is permissive — routing rejects unknown roles at
        dispatch time with a fall-through to glob routing, so the model
        layer doesn't constrain the role vocabulary. Goes through
        ``model_validate`` so ``extra="forbid"`` is exercised."""
        for role in ("pm", "sa", "dev", "test", "document", "spec"):
            payload = {
                "type": "pattern-divergence",
                "severity": "important",
                "reviewer": "reviewer-pattern-conformance",
                "prose": "x",
                "confidence": 0.8,
                "target_role": role,
            }
            c = ReviewerComment.model_validate(payload)
            assert c.target_role == role

    def test_existing_records_load_without_target_role(self) -> None:
        """JSONL records from before this field shipped must still load."""
        legacy_payload = {
            "type": "pattern-divergence",
            "severity": "important",
            "reviewer": "reviewer-pattern-conformance",
            "prose": "x",
            "confidence": 0.8,
        }
        c = ReviewerComment.model_validate(legacy_payload)
        assert c.target_role is None


# ---- format_comment_markdown --------------------------------------------


class TestMarkdownRendering:
    def test_minimal_comment_renders(self) -> None:
        out = format_comment_markdown(_comment())
        assert "[IMPORTANT]" in out
        assert "reviewer-pattern-conformance" in out
        assert "pattern-divergence" in out

    def test_anchor_uses_file_line_when_both_present(self) -> None:
        out = format_comment_markdown(_comment(file="jig/foo.py", line=42))
        assert "anchor: jig/foo.py:42" in out

    def test_anchor_falls_back_to_file_when_no_line(self) -> None:
        out = format_comment_markdown(_comment(file="jig/foo.py", line=None))
        assert "anchor: jig/foo.py" in out

    def test_anchor_falls_back_to_contract_uri(self) -> None:
        out = format_comment_markdown(
            _comment(
                file=None,
                line=None,
                contract_uri="project://arch/contracts#x",
            )
        )
        assert "anchor: project://arch/contracts#x" in out

    def test_no_anchor_omits_anchor_line(self) -> None:
        out = format_comment_markdown(
            _comment(file=None, line=None, contract_uri=None)
        )
        assert "anchor:" not in out

    def test_metadata_line_includes_confidence_and_cadence_and_cycle(
        self,
    ) -> None:
        out = format_comment_markdown(_comment(confidence=0.73))
        assert "confidence: 0.73" in out
        assert "cadence: end_of_ticket" in out
        assert "cycle: 0" in out

    def test_auto_apply_window_appears_when_set(self) -> None:
        out = format_comment_markdown(
            _comment(
                suggested_diff="--- a/foo\n+++ b/foo\n@@\n-x\n+y\n",
                auto_apply_after=300,
            )
        )
        assert "auto-apply in: 300s" in out

    def test_auto_apply_window_omitted_when_unset(self) -> None:
        out = format_comment_markdown(_comment())
        assert "auto-apply" not in out

    def test_prose_rendered_as_blockquote(self) -> None:
        out = format_comment_markdown(_comment(prose="hello world"))
        assert "> hello world" in out

    def test_multiline_prose_rendered_per_line(self) -> None:
        out = format_comment_markdown(_comment(prose="line one\nline two"))
        assert "> line one" in out
        assert "> line two" in out

    def test_suggested_diff_rendered_in_codefence(self) -> None:
        out = format_comment_markdown(
            _comment(
                suggested_diff="-old line\n+new line\n",
            )
        )
        assert "```diff" in out
        assert "-old line" in out
        assert "+new line" in out

    def test_long_diff_truncated(self) -> None:
        diff = "\n".join(f"+line {i}" for i in range(50))
        out = format_comment_markdown(_comment(suggested_diff=diff))
        assert "truncated to 20 of 50 lines" in out

    def test_no_diff_no_codefence(self) -> None:
        out = format_comment_markdown(_comment())
        assert "```diff" not in out

    def test_evidence_section_appears_when_populated(self) -> None:
        out = format_comment_markdown(
            _comment(
                evidence=[
                    Evidence(
                        source="diff",
                        reference="jig/foo.py:12",
                        excerpt="duplicate helper",
                    )
                ]
            )
        )
        assert "evidence:" in out
        assert "[diff]" in out
        assert "jig/foo.py:12" in out

    def test_evidence_omitted_when_empty(self) -> None:
        out = format_comment_markdown(_comment())
        assert "evidence:" not in out

    def test_evidence_excerpt_truncated_when_long(self) -> None:
        big = "x" * 300
        out = format_comment_markdown(
            _comment(
                evidence=[
                    Evidence(
                        source="test_run",
                        reference="tests/x.py",
                        excerpt=big,
                    )
                ]
            )
        )
        assert "..." in out


# ---- evidence aggregation across a list ---------------------------------


class TestEvidenceAggregation:
    def test_multiple_comments_each_carry_independent_evidence(self) -> None:
        c1 = _comment(
            evidence=[
                Evidence(source="diff", reference="jig/foo.py:12"),
            ]
        )
        c2 = _comment(
            reviewer="reviewer-error-handling",
            evidence=[
                Evidence(
                    source="static_analysis",
                    reference="ruff:E501",
                    excerpt="line too long",
                ),
                Evidence(
                    source="spec_text",
                    reference="project://arch/contracts#a",
                ),
            ],
        )
        all_evidence: list[Evidence] = []
        for c in (c1, c2):
            all_evidence.extend(c.evidence)
        assert len(all_evidence) == 3
        # Sources preserved.
        assert {e.source for e in all_evidence} == {
            "diff",
            "static_analysis",
            "spec_text",
        }

    def test_evidence_round_trip_through_jsonl(self) -> None:
        """Pydantic round-trip preserves the inner Evidence list shape."""
        c = _comment(
            evidence=[
                Evidence(source="diff", reference="x"),
                Evidence(source="spec_text", reference="y", excerpt="z"),
            ]
        )
        as_json = c.model_dump_json()
        restored = ReviewerComment.model_validate_json(as_json)
        assert restored.evidence[0].source == "diff"
        assert restored.evidence[1].excerpt == "z"
