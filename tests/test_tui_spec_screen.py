from datetime import datetime, timezone
from pathlib import Path

import pytest

from jig.tui.app import JigApp


def _spec_dump(
    name: str = "test",
    caps: list[dict] | None = None,
    non_goals: list[dict] | None = None,
) -> dict:
    """Build a fake StructuredSpec dict matching the wire shape."""
    return {
        "name": name,
        "summary": "summary line",
        "capabilities": caps or [],
        "non_goals": non_goals or [],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "spec_version": 1,
    }


def _cap(id: str, title: str, state: str = "planned", **kw) -> dict:
    base = {
        "id": id,
        "title": title,
        "state": state,
        "summary": "",
        "user_story": None,
        "behaviors": [],
        "acceptance_criteria": [],
        "excluded": [],
        "open_questions": [],
        "tickets": [],
        "aliases": [],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "state_changed_at": datetime.now(timezone.utc).isoformat(),
    }
    base.update(kw)
    return base


@pytest.mark.asyncio
async def test_spec_screen_renders_snapshot(tmp_path: Path):
    from jig.tui.screens.spec import SpecScreen
    from textual.widgets import Tree

    app = JigApp(project_path=tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("ctrl+3")  # Spec pane
        screen = app.query_one(SpecScreen)
        await screen.handle_snapshot(
            _spec_dump(
                "myproj",
                caps=[_cap("a", "Alpha", "backlog"), _cap("b", "Beta", "planned")],
                non_goals=[
                    {"id": "ng1", "text": "no x", "rationale": "", "aliases": []}
                ],
            )
        )
        await pilot.pause(0.05)
        tree = app.query_one("#spec-tree", Tree)
        # Root has children for each state group + non-goals
        labels = [str(child.label) for child in tree.root.children]
        # Backlog, Planned, In Progress, Built, Archived, Non-Goals
        assert any("Backlog" in lb for lb in labels)
        assert any("Planned" in lb for lb in labels)
        assert any("Non-Goals" in lb for lb in labels)


@pytest.mark.asyncio
async def test_spec_screen_handles_null_snapshot(tmp_path: Path):
    from jig.tui.screens.spec import SpecScreen

    app = JigApp(project_path=tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("ctrl+3")
        screen = app.query_one(SpecScreen)
        await screen.handle_snapshot(None)
        await pilot.pause(0.05)
        # No crash; detail shows "No spec yet"


@pytest.mark.asyncio
async def test_app_routes_spec_snapshot_to_screen(tmp_path: Path):
    from textual.widgets import Tree

    app = JigApp(project_path=tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("ctrl+3")
        await app._handle_daemon_message(
            {
                "type": "snapshot",
                "topic": "spec",
                "data": _spec_dump("p1", caps=[_cap("a", "Alpha")]),
            }
        )
        await pilot.pause(0.05)
        tree = app.query_one("#spec-tree", Tree)
        labels = [str(child.label) for child in tree.root.children]
        assert any("Planned" in lb for lb in labels)


@pytest.mark.asyncio
async def test_r_hotkey_pushes_raw_yaml_modal(tmp_path: Path):
    from jig.tui.screens.spec import SpecScreen
    from jig.tui.screens.spec_modals import RawYamlModal

    app = JigApp(project_path=tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("ctrl+3")
        screen = app.query_one(SpecScreen)
        await screen.handle_snapshot(_spec_dump("p1"))
        await pilot.pause(0.05)
        await pilot.press("r")
        await pilot.pause(0.05)
        assert isinstance(app.screen, RawYamlModal)
        await pilot.press("escape")


@pytest.mark.asyncio
async def test_b_on_spec_pane_pushes_brief_modal(tmp_path: Path):
    from jig.tui.screens.spec_modals import BriefModal

    app = JigApp(project_path=tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("ctrl+3")
        await pilot.press("b")
        await pilot.pause(0.05)
        assert isinstance(app.screen, BriefModal)
        await pilot.press("escape")


@pytest.mark.asyncio
async def test_b_on_tickets_pane_still_toggles_board(tmp_path: Path):
    """Confirm the b binding doesn't accidentally trigger BriefModal on Tickets."""
    from jig.tui.screens.spec_modals import BriefModal

    app = JigApp(project_path=tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("ctrl+2")  # Tickets pane
        await pilot.press("b")
        await pilot.pause(0.05)
        # Should NOT be BriefModal
        assert not isinstance(app.screen, BriefModal)
