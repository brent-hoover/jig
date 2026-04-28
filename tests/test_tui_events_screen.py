from datetime import datetime, timezone
from pathlib import Path

import pytest

from jig.tui.app import JigApp


def _ev(kind: str, ticket_id: str = "t1", topic: str = "tickets.t1",
        ts: datetime | None = None) -> dict:
    return {
        "id": f"ev-{ticket_id}-{kind}",
        "from": "test",
        "to": "broadcast",
        "type": "context_update",
        "topic": topic,
        "payload": {"kind": kind, "ticket_id": ticket_id},
        "timestamp": (ts or datetime.now(timezone.utc)).isoformat(),
        "correlation_id": None,
    }


@pytest.mark.asyncio
async def test_events_screen_renders_snapshot(tmp_path: Path):
    from jig.tui.screens.events import EventsScreen
    from textual.widgets import ListView

    app = JigApp(project_path=tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("4")  # Events pane
        screen = app.query_one(EventsScreen)
        await screen.handle_snapshot([
            _ev("ticket_created"),
            _ev("ticket_updated"),
            _ev("comment_posted"),
        ])
        await pilot.pause(0.05)
        list_view = app.query_one("#events-list", ListView)
        assert len(list_view.children) == 3


@pytest.mark.asyncio
async def test_f_cycles_filter(tmp_path: Path):
    from jig.tui.screens.events import EventsScreen
    from textual.widgets import ListView

    app = JigApp(project_path=tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("4")
        screen = app.query_one(EventsScreen)
        await screen.handle_snapshot([
            _ev("ticket_created"),
            _ev("comment_posted"),
            _ev("agent_run"),
        ])
        await pilot.pause(0.05)
        # all → 3
        list_view = app.query_one("#events-list", ListView)
        assert len(list_view.children) == 3
        # press f → ticket_* → 1
        await pilot.press("f")
        await pilot.pause(0.05)
        assert len(list_view.children) == 1
        # press f → comment_* → 1
        await pilot.press("f")
        await pilot.pause(0.05)
        assert len(list_view.children) == 1
        # press f → agent_* → 1
        await pilot.press("f")
        await pilot.pause(0.05)
        assert len(list_view.children) == 1


@pytest.mark.asyncio
async def test_F_toggles_follow(tmp_path: Path):
    from jig.tui.screens.events import EventsScreen

    app = JigApp(project_path=tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("4")
        screen = app.query_one(EventsScreen)
        # follow defaults to True
        assert screen.follow is True
        await pilot.press("F")
        await pilot.pause(0.05)
        assert screen.follow is False


@pytest.mark.asyncio
async def test_enter_opens_event_detail_modal(tmp_path: Path):
    from jig.tui.screens.events import EventsScreen
    from jig.tui.screens.event_detail_modal import EventDetailModal

    app = JigApp(project_path=tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("4")
        screen = app.query_one(EventsScreen)
        await screen.handle_snapshot([_ev("ticket_created")])
        await pilot.pause(0.05)
        await pilot.press("enter")
        await pilot.pause(0.05)
        assert isinstance(app.screen, EventDetailModal)
        await pilot.press("escape")


@pytest.mark.asyncio
async def test_enter_on_now_pane_does_not_open_event_modal(tmp_path: Path):
    """Sanity: Enter on the Now pane should still go to Input.submit, not
    pop the event modal."""
    from jig.tui.screens.event_detail_modal import EventDetailModal

    app = JigApp(project_path=tmp_path)
    async with app.run_test() as pilot:
        # Stay on Now pane
        await pilot.press("h", "i", "enter")
        await pilot.pause(0.05)
        assert not isinstance(app.screen, EventDetailModal)


@pytest.mark.asyncio
async def test_app_routes_events_snapshot(tmp_path: Path):
    from textual.widgets import ListView

    app = JigApp(project_path=tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("4")
        await app._handle_daemon_message({
            "type": "snapshot",
            "topic": "events",
            "data": [_ev("ticket_created"), _ev("scaffold_applied")],
        })
        await pilot.pause(0.05)
        list_view = app.query_one("#events-list", ListView)
        assert len(list_view.children) == 2
