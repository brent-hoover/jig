"""Tests for ``jig.tui.ligature_safe``."""

from __future__ import annotations

from pathlib import Path

import pytest

from jig.tui import ZWSP as _ZWSP, ligature_safe


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
    # `t.get("title", "(untitled)")` returns None when the dict has
    # {"title": None}. The function's guard short-circuits on falsy input.
    assert ligature_safe(None) is None  # type: ignore[arg-type]


def test_zwsp_is_zero_width_visible_length_preserved() -> None:
    # ZWSP is non-printing; ``len()`` will grow by one per inserted ZWSP, but
    # the rendered width is unchanged. This test pins the count so a regression
    # to a wider replacement character (e.g. NBSP) would fail loudly.
    src = "--a--b"
    out = ligature_safe(src)
    # Two '--' pairs → two ZWSPs inserted.
    assert out.count(_ZWSP) == 2
    assert len(out) == len(src) + 2


def test_idempotent() -> None:
    """Re-applying ligature_safe must not introduce new ZWSPs. Render-
    time callers can compose this safely without worrying about
    repeated invocation."""
    s = "Filtering: --min-score -> result"
    once = ligature_safe(s)
    twice = ligature_safe(once)
    assert once == twice


def test_compound_pair_both_broken() -> None:
    """'-->' contains both '--' and '->'. After the run, neither pair
    should survive as a font-ligatable sequence."""
    out = ligature_safe("-->")
    assert "--" not in out
    assert "->" not in out


def test_runs_of_three_or_more_fully_broken() -> None:
    """str.replace is non-overlapping in a single pass — '===' would
    leave a second '==' untouched without the stabilize loop. Pin
    that the loop catches the residual."""
    out = ligature_safe("===")
    assert "==" not in out


@pytest.mark.asyncio
async def test_clipboard_strips_zwsp(tmp_path: Path) -> None:
    """JigApp.copy_to_clipboard must strip the ligature-breaking ZWSPs
    before writing to the system clipboard. Without this, pasting a
    copied "--limit" into a shell would yield "-<ZWSP>-limit" and the
    command wouldn't parse."""
    from jig.tui.app import JigApp

    app = JigApp(project_path=tmp_path)
    async with app.run_test():
        app.copy_to_clipboard(ligature_safe("--limit 10"))
        # The local clipboard property captures what was copied.
        assert _ZWSP not in app.clipboard
        assert app.clipboard == "--limit 10"
