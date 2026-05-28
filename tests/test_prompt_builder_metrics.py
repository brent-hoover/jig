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
