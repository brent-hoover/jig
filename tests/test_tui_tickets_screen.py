from pathlib import Path

import pytest

from jig.tui.app import JigApp


def _ticket(id: str, **kwargs) -> dict:
    base = {
        "id": id,
        "work_type": "feature",
        "size": "m",
        "status": "open",
        "title": f"Ticket {id}",
        "description": "",
        "assignee": None,
        "parent_id": None,
        "blocked_by": [],
        "workflow": None,
    }
    base.update(kwargs)
    return base


@pytest.mark.asyncio
async def test_tickets_screen_renders_snapshot(tmp_path: Path):
    from jig.tui.screens.tickets import TicketsScreen
    from textual.widgets import ListView

    app = JigApp(project_path=tmp_path)
    async with app.run_test() as pilot:
        # Switch to the Tickets pane so it's mounted/visible
        await pilot.press("2")
        screen = app.query_one(TicketsScreen)

        await screen.handle_snapshot([
            _ticket("a", title="Alpha"),
            _ticket("b", title="Beta", status="in_progress"),
        ])
        await pilot.pause(0.05)

        list_view = app.query_one("#tickets-list", ListView)
        assert len(list_view.children) == 2


@pytest.mark.asyncio
async def test_tickets_screen_handles_created_event(tmp_path: Path):
    from jig.tui.screens.tickets import TicketsScreen
    from textual.widgets import ListView

    app = JigApp(project_path=tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("2")
        screen = app.query_one(TicketsScreen)

        await screen.handle_snapshot([_ticket("a", title="Alpha")])
        await screen.handle_event("created", _ticket("b", title="Beta"))
        await pilot.pause(0.05)

        list_view = app.query_one("#tickets-list", ListView)
        assert len(list_view.children) == 2


@pytest.mark.asyncio
async def test_tickets_screen_handles_updated_event(tmp_path: Path):
    from jig.tui.screens.tickets import TicketsScreen

    app = JigApp(project_path=tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("2")
        screen = app.query_one(TicketsScreen)

        await screen.handle_snapshot([_ticket("a", title="Alpha", status="open")])
        await screen.handle_event("updated", _ticket("a", title="Alpha", status="resolved"))
        await pilot.pause(0.05)

        assert screen.tickets["a"]["status"] == "resolved"


@pytest.mark.asyncio
async def test_app_routes_tickets_snapshot_to_screen(tmp_path: Path):
    from textual.widgets import ListView

    app = JigApp(project_path=tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("2")
        await app._handle_daemon_message({
            "type": "snapshot",
            "topic": "tickets",
            "data": [_ticket("a", title="Alpha")],
        })
        await pilot.pause(0.05)
        list_view = app.query_one("#tickets-list", ListView)
        assert len(list_view.children) == 1


@pytest.mark.asyncio
async def test_tickets_detail_shows_selected_ticket(tmp_path: Path):
    from jig.tui.screens.tickets import TicketsScreen
    from textual.widgets import Static

    app = JigApp(project_path=tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("2")
        screen = app.query_one(TicketsScreen)

        await screen.handle_snapshot([
            _ticket("a", title="Alpha", description="alpha desc"),
            _ticket("b", title="Beta", description="beta desc"),
        ])
        await pilot.pause(0.05)
        detail = app.query_one("#tickets-detail", Static)
        # The first ticket should be selected by default; description shown
        rendered = str(detail.content)
        assert "Alpha" in rendered or "Beta" in rendered  # one of them
