"""Smoke tests for the TUI shell.

These don't run the TUI in a real terminal (Textual provides a
``Pilot`` for headless testing). They verify the app composes
correctly, screens are mounted, and tab navigation works.
"""
from pathlib import Path

import pytest

from jig.tui.app import JigApp


@pytest.mark.asyncio
async def test_app_composes_with_four_screens(tmp_path: Path):
    app = JigApp(project_path=tmp_path)
    async with app.run_test() as pilot:  # noqa: F841
        for screen_id in ("now-pane", "tickets-pane", "spec-pane", "events-pane"):
            assert app.query_one(f"#{screen_id}") is not None


@pytest.mark.asyncio
async def test_app_switches_screens_via_hotkey(tmp_path: Path):
    app = JigApp(project_path=tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("2")
        tabs = app.query_one("TabbedContent")
        assert tabs.active == "tickets-pane"
        await pilot.press("1")
        assert tabs.active == "now-pane"
