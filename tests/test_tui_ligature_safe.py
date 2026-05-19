"""Tests for ``jig.tui.ligature_safe``."""

from __future__ import annotations

from jig.tui import ligature_safe

_ZWSP = "​"


def test_double_hyphen_broken_by_zwsp() -> None:
    # The original failure mode: '--limit N' rendered as long-dash by FiraCode.
    assert ligature_safe("--limit N") == f"-{_ZWSP}-limit N"


def test_multiple_double_hyphens_all_broken() -> None:
    src = "Filtering: `--min-score` and `--type` flags"
    out = ligature_safe(src)
    assert "--" not in out
    # Single dashes are untouched (so 'min-score' stays as-is).
    assert "min-score" in out
    assert "type" in out


def test_other_ligature_pairs_broken() -> None:
    assert ligature_safe("a -> b") == f"a -{_ZWSP}> b"
    assert ligature_safe("a => b") == f"a ={_ZWSP}> b"
    assert ligature_safe("x >= y") == f"x >{_ZWSP}= y"
    assert ligature_safe("x <= y") == f"x <{_ZWSP}= y"
    assert ligature_safe("x != y") == f"x !{_ZWSP}= y"
    assert ligature_safe("x == y") == f"x ={_ZWSP}= y"
    assert ligature_safe("std::move") == f"std:{_ZWSP}:move"


def test_no_ligature_pair_passes_through_unchanged() -> None:
    assert ligature_safe("Integration & validation") == "Integration & validation"
    assert ligature_safe("Core: CLI skeleton") == "Core: CLI skeleton"


def test_empty_and_none_like_inputs() -> None:
    assert ligature_safe("") == ""


def test_zwsp_is_zero_width_visible_length_preserved() -> None:
    # ZWSP is non-printing; ``len()`` will grow by one per inserted ZWSP, but
    # the rendered width is unchanged. This test pins the count so a regression
    # to a wider replacement character (e.g. NBSP) would fail loudly.
    src = "--a--b"
    out = ligature_safe(src)
    # Two '--' pairs → two ZWSPs inserted.
    assert out.count(_ZWSP) == 2
    assert len(out) == len(src) + 2
