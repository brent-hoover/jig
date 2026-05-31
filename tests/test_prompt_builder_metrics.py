"""Tests for the Objective Code Metrics reviewer-prompt section
(radon-quality-signals)."""

from __future__ import annotations

from jig.code_metrics import ChangeMetrics
from jig.prompt_builder import _code_metrics_section


def test_none_renders_nothing() -> None:
    assert _code_metrics_section(None) == ""


def test_flagged_metrics_render_block_with_high_annotation() -> None:
    m = ChangeMetrics(
        max_cc=12,
        max_cc_location="foo.py:handle_request",
        ruff_findings=0,
        loc_delta=340,
    )
    assert m.flagged is True  # derived from max_cc > threshold
    out = _code_metrics_section(m)

    assert "## Objective Code Metrics" in out
    assert "12" in out
    assert "foo.py:handle_request" in out
    assert "HIGH" in out  # flagged annotation
    assert "ruff findings: 0" in out
    assert "+340" in out  # signed LoC delta


def test_unflagged_metrics_render_without_high_annotation() -> None:
    m = ChangeMetrics(
        max_cc=4,
        max_cc_location="foo.py:small",
        ruff_findings=2,
        loc_delta=-15,
    )
    assert m.flagged is False
    out = _code_metrics_section(m)

    assert "## Objective Code Metrics" in out
    assert "4" in out
    assert "HIGH" not in out
    assert "ruff findings: 2" in out
    assert "-15" in out  # net deletion renders signed


def test_no_functions_renders_without_location() -> None:
    m = ChangeMetrics(
        max_cc=0,
        max_cc_location=None,
        ruff_findings=0,
        loc_delta=3,
    )
    out = _code_metrics_section(m)

    assert "## Objective Code Metrics" in out
    assert "max cyclomatic complexity: 0" in out


def test_section_renders_per_reviewer_taxonomy_block() -> None:
    from jig.code_quality.taxonomy import TaxonomyHit

    metrics = ChangeMetrics(
        max_cc=3,
        max_cc_location="m.py:f",
        ruff_findings=0,
        loc_delta=5,
        taxonomy_hits=(
            TaxonomyHit(
                id="TAX-SEC-001",
                category="security",
                file="m.py",
                line=12,
                reviewer="reviewer-security",
            ),
            TaxonomyHit(
                id="TAX-LANG-001",
                category="language-pitfall",
                file="m.py",
                line=20,
                reviewer="reviewer-pattern-conformance",
            ),
        ),
    )
    out_sec = _code_metrics_section(metrics, role="reviewer-security")
    out_pc = _code_metrics_section(metrics, role="reviewer-pattern-conformance")

    # Universal piece still rendered for both.
    assert "## Objective Code Metrics" in out_sec
    assert "## Objective Code Metrics" in out_pc

    # Per-reviewer deterministic block: security sees its hit, not pc's.
    assert "TAX-SEC-001" in out_sec
    assert "TAX-LANG-001" not in out_sec
    assert "TAX-LANG-001" in out_pc
    assert "TAX-SEC-001" not in out_pc

    # Per-reviewer judgment checklist: pc owns plenty of judgment items;
    # security owns none (its three entries are all ``detection: ruff``).
    assert "Judgment checklist" in out_pc
    assert "TAX-CF-001" in out_pc  # off-by-one belongs to pc
    assert "Judgment checklist" not in out_sec


def test_section_for_non_reviewer_role_omits_per_reviewer_block() -> None:
    metrics = ChangeMetrics(
        max_cc=0, max_cc_location=None, ruff_findings=0, loc_delta=0, taxonomy_hits=()
    )
    out = _code_metrics_section(metrics, role="dev")  # not in known_llm_reviewer_ids()

    # Universal piece renders; per-reviewer pieces don't.
    assert "## Objective Code Metrics" in out
    assert "Judgment checklist" not in out
    assert "Deterministic taxonomy findings" not in out
