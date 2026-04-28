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
        await pilot.press("ctrl+2")
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
        await pilot.press("ctrl+2")
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
        await pilot.press("ctrl+2")
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
        await pilot.press("ctrl+2")
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
        await pilot.press("ctrl+2")
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


@pytest.mark.asyncio
async def test_tickets_screen_toggles_to_board_view(tmp_path: Path):
    from jig.tui.screens.tickets import TicketsScreen
    from textual.widgets import Static

    app = JigApp(project_path=tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("ctrl+2")
        screen = app.query_one(TicketsScreen)
        await screen.handle_snapshot([
            _ticket("a", title="Alpha", status="open"),
            _ticket("b", title="Beta", status="in_progress"),
            _ticket("c", title="Gamma", status="resolved"),
        ])
        await pilot.pause(0.05)

        # Initially in list mode: list-mode-row is visible
        list_row = app.query_one("#list-mode-row")
        board = app.query_one("#board-mode-view")
        assert list_row.display is True
        assert board.display is False

        # Toggle to board
        await pilot.press("b")
        await pilot.pause(0.05)
        assert list_row.display is False
        assert board.display is True

        # Each status column should reflect ticket counts
        open_hdr = app.query_one("#hdr-open", Static)
        ip_hdr = app.query_one("#hdr-in_progress", Static)
        resolved_hdr = app.query_one("#hdr-resolved", Static)
        assert "(1)" in str(open_hdr.content)
        assert "(1)" in str(ip_hdr.content)
        assert "(1)" in str(resolved_hdr.content)


@pytest.mark.asyncio
async def test_board_view_groups_merge_conflict_under_blocked(tmp_path: Path):
    from jig.tui.screens.tickets import TicketsScreen
    from textual.widgets import Static

    app = JigApp(project_path=tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("ctrl+2")
        screen = app.query_one(TicketsScreen)
        await screen.handle_snapshot([
            _ticket("a", title="A", status="blocked"),
            _ticket("b", title="B", status="merge_conflict"),
        ])
        await pilot.pause(0.05)
        await pilot.press("b")
        await pilot.pause(0.05)

        blocked_hdr = app.query_one("#hdr-blocked", Static)
        assert "(2)" in str(blocked_hdr.content)


@pytest.mark.asyncio
async def test_board_view_updates_when_tickets_change(tmp_path: Path):
    from jig.tui.screens.tickets import TicketsScreen
    from textual.widgets import Static

    app = JigApp(project_path=tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("ctrl+2")
        screen = app.query_one(TicketsScreen)
        await screen.handle_snapshot([])
        await pilot.press("b")
        await pilot.pause(0.05)
        # Now in board mode, push an event
        await screen.handle_event("created", _ticket("x", title="X", status="in_progress"))
        await pilot.pause(0.05)
        ip_hdr = app.query_one("#hdr-in_progress", Static)
        assert "(1)" in str(ip_hdr.content)


@pytest.mark.asyncio
async def test_b_hotkey_toggles_back_to_list(tmp_path: Path):
    app = JigApp(project_path=tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("ctrl+2")
        await pilot.press("b")
        await pilot.pause(0.05)
        await pilot.press("b")
        await pilot.pause(0.05)
        list_row = app.query_one("#list-mode-row")
        board = app.query_one("#board-mode-view")
        assert list_row.display is True
        assert board.display is False


@pytest.mark.asyncio
async def test_n_hotkey_pushes_new_ticket_modal(tmp_path: Path):
    from jig.tui.screens.ticket_form import NewTicketModal

    app = JigApp(project_path=tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("ctrl+2")  # Tickets pane
        await pilot.pause(0.05)
        await pilot.press("n")
        await pilot.pause(0.05)
        assert isinstance(app.screen, NewTicketModal)
        # Cancel out
        await pilot.press("escape")


@pytest.mark.asyncio
async def test_e_hotkey_does_nothing_with_no_selection(tmp_path: Path):
    """e on Tickets pane with no tickets should not crash."""
    app = JigApp(project_path=tmp_path)
    # Force the daemon client at an unreachable port so a real daemon
    # running on the default 9100 (e.g. operator's dogfood project) can't
    # populate screen.tickets and make this test pass-modal unexpectedly.
    app.client.addr_provider = lambda: "ws://127.0.0.1:1"
    async with app.run_test() as pilot:
        await pilot.press("ctrl+2")
        await pilot.press("e")
        await pilot.pause(0.05)
        # No modal should be active
        from jig.tui.screens.ticket_form import EditTicketModal
        assert not isinstance(app.screen, EditTicketModal)


@pytest.mark.asyncio
async def test_e_hotkey_pushes_edit_modal_with_selection(tmp_path: Path):
    from jig.tui.screens.tickets import TicketsScreen
    from jig.tui.screens.ticket_form import EditTicketModal

    app = JigApp(project_path=tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("ctrl+2")
        screen = app.query_one(TicketsScreen)
        await screen.handle_snapshot([_ticket("a", title="Alpha", status="open")])
        await pilot.pause(0.05)
        await pilot.press("e")
        await pilot.pause(0.05)
        assert isinstance(app.screen, EditTicketModal)
        await pilot.press("escape")
