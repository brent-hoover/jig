"""Repro for the scrollback corruption seen with PM planning text.

The PM agent emits two ``agents/text`` events during planning:

1. A short narrative sentence.
2. A long single-event payload containing narrative + an inline
   Markdown table + Unicode ambiguous-width characters (``≤``, ``→``,
   ``—``).

The visible symptom in the running TUI: the narrative line is
truncated mid-word (``...post it for approv``) and small word
fragments (``by-s``, ``ing``, ``kets``) appear in the gutter between
the scrollback panel and the right-docked Sidebar.

This test reproduces the path deterministically: it drives the real
``NowScreen.handle_daemon_event`` with the exact text strings pulled
from a real eval log run, then inspects the rendered ``RichLog``
strips. The first part of the test asserts what the buffer looks
like in isolation; the second part asserts what we EXPECT after a
fix (no character drops mid-word).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.widgets import RichLog

from jig.tui.app import JigApp
from jig.tui.screens.now import NowScreen


# Verbatim from /Users/brent/Projects/personal/jig_evals/hn-cli/.jig/logs/
# jig-20260522-130221.jsonl — the two ``[pm:planning] text:`` log
# lines from the run that produced the screenshot the user shared.
PM_TEXT_1 = (
    "Now I have the full picture. Let me draft the plan and post it "
    "for approval."
)
PM_TEXT_2 = (
    "Here's the plan I've drafted: **4 tickets, linear chain** "
    "(≤10 tickets → no parallelism needed): "
    "| # | Title | Type | Size | Depends on | "
    "|---|-------|------|------|------------| "
    "| 1 | Core: project setup + `hn-cli top --limit N` "
    "| feature | m | — | "
    "| 2 | Filtering: `--min-score` and `--type` flags "
    "| feature | s | 1 | "
    "| 3 | JSON output: `--format json` | feature | s | 2 | "
    "| 4 | Integration validation | feature | m (validation) | 3 |"
)


def _strips_to_text(scrollback: RichLog) -> list[str]:
    """Return each scrollback line as a plain-text string (no styling)."""
    out: list[str] = []
    for strip in scrollback.lines:
        # Each strip is a list of Segment objects; concatenate their text.
        line = "".join(seg.text for seg in strip)
        out.append(line)
    return out


@pytest.mark.asyncio
async def test_pm_planning_text_does_not_drop_chars(tmp_path: Path) -> None:
    """The narrative sentence ``...post it for approval.`` must appear
    intact in the rendered scrollback — across wrap boundaries.

    Pre-fix: the visible output drops 1-4 characters at the wrap
    boundary (e.g. ``approv`` followed directly by the next event's
    content with no ``al.`` in between).
    """
    # Use a narrow size that mirrors the user's screenshot (~95-cell
    # terminal with a 36-cell right-docked sidebar leaves the
    # scrollback ~55-60 cells wide).
    app = JigApp(project_path=tmp_path)
    async with app.run_test(size=(95, 30)):
        now = app.query_one(NowScreen)
        await now.handle_daemon_event(
            {
                "type": "event",
                "topic": "agents",
                "kind": "text",
                "data": {"role": "pm", "text": PM_TEXT_1},
            }
        )
        await now.handle_daemon_event(
            {
                "type": "event",
                "topic": "agents",
                "kind": "text",
                "data": {"role": "pm", "text": PM_TEXT_2},
            }
        )
        scrollback = app.query_one("#scrollback", RichLog)
        lines = _strips_to_text(scrollback)
        flat = " ".join(lines)
        # Sanity: PM_TEXT_1's last word must be intact somewhere.
        assert "approval." in flat, (
            "PM_TEXT_1's tail 'approval.' was dropped during render. "
            f"Got lines:\n  " + "\n  ".join(repr(line) for line in lines)
        )


@pytest.mark.asyncio
async def test_pm_planning_text_unicode_preserved(tmp_path: Path) -> None:
    """Ambiguous-width Unicode chars (``≤``, ``→``, ``—``) must survive
    the render — they're East Asian Width 'A' and trigger Rich's
    cell-length miscount when the terminal renders them as width 2."""
    app = JigApp(project_path=tmp_path)
    async with app.run_test(size=(95, 30)):
        now = app.query_one(NowScreen)
        await now.handle_daemon_event(
            {
                "type": "event",
                "topic": "agents",
                "kind": "text",
                "data": {"role": "pm", "text": PM_TEXT_2},
            }
        )
        scrollback = app.query_one("#scrollback", RichLog)
        lines = _strips_to_text(scrollback)
        flat = "".join(lines)
        # Each ambiguous-width char must appear at least once.
        for ch, name in (("≤", "less-than-or-equal"), ("→", "arrow"), ("—", "em-dash")):
            assert ch in flat, (
                f"Unicode {name} ({ch!r}) dropped during render. "
                f"Got lines:\n  " + "\n  ".join(repr(line) for line in lines)
            )


@pytest.mark.asyncio
async def test_pm_planning_text_no_lines_exceed_widget_width(
    tmp_path: Path,
) -> None:
    """No rendered scrollback line should be visibly wider than the
    widget's content area. Lines wider than that bleed past the right
    edge into the Sidebar's gutter (the visible 'by-s' / 'kets'
    fragments in the screenshot).
    """
    app = JigApp(project_path=tmp_path)
    async with app.run_test(size=(95, 30)):
        now = app.query_one(NowScreen)
        await now.handle_daemon_event(
            {
                "type": "event",
                "topic": "agents",
                "kind": "text",
                "data": {"role": "pm", "text": PM_TEXT_2},
            }
        )
        scrollback = app.query_one("#scrollback", RichLog)
        widget_width = scrollback.scrollable_content_region.width
        offenders: list[tuple[int, int, str]] = []
        for idx, strip in enumerate(scrollback.lines):
            cell_len = sum(seg.cell_length for seg in strip)
            if cell_len > widget_width:
                line_text = "".join(seg.text for seg in strip)
                offenders.append((idx, cell_len, line_text))
        assert not offenders, (
            f"Widget width is {widget_width} cells; "
            f"{len(offenders)} line(s) exceed it (would bleed into the "
            "Sidebar gutter):\n  "
            + "\n  ".join(
                f"line {i}: cell_len={n}, text={text!r}"
                for i, n, text in offenders
            )
        )
