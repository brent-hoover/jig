"""Tickets screen — master/detail of all tickets, live-updated.

Left pane: scrollable list of tickets sorted by most recent activity
(falls back to creation order if no activity timestamp). Right pane:
detail view of the focused ticket (status, work type, size, title,
description, assignee, parent_id, blocked_by, workflow).

Hotkeys (Tickets pane only):
  j / down       move selection down
  k / up         move selection up
  enter          focus the detail pane (so its bindings respond)
  escape         return focus to the list

Live data:
  - On mount: state empty until snapshot arrives
  - Snapshot replaces state wholesale
  - tickets/created event adds the new ticket
  - tickets/updated event replaces the existing ticket by id
"""
from __future__ import annotations

from typing import Any

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import ListItem, ListView, Static


_STATUS_ICON = {
    "open": "○",
    "in_progress": "◉",
    "blocked": "⊘",
    "needs_info": "?",
    "failed": "✗",
    "merge_conflict": "⚠",
    "resolved": "✓",
    "closed": "·",
}

_STATUS_COLOR = {
    "open": "#888888",
    "in_progress": "#ffcc00",
    "blocked": "#ff8800",
    "needs_info": "#ff00ff",
    "failed": "#cc0000",
    "merge_conflict": "#ff8800",
    "resolved": "#00cc00",
    "closed": "#444444",
}


class TicketsScreen(Screen):
    """Live master/detail of all tickets."""

    AUTO_FOCUS = None  # mirror NowScreen — prevent TabPane focus loops

    BINDINGS = [
        Binding("j", "select_next", "Down", show=False),
        Binding("k", "select_prev", "Up", show=False),
    ]

    DEFAULT_CSS = """
    TicketsScreen {
        layout: vertical;
    }
    TicketsScreen > Horizontal {
        height: 1fr;
    }
    #tickets-list {
        width: 50;
        border-right: solid $accent;
    }
    #tickets-detail {
        padding: 1 2;
        height: 1fr;
    }
    """

    tickets: reactive[dict[str, dict[str, Any]]] = reactive({}, recompose=False)

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield ListView(id="tickets-list")
            yield Static(
                "[dim]waiting for tickets…[/dim]",
                id="tickets-detail",
                markup=True,
            )

    def on_show(self) -> None:
        # Focus the ListView when the pane becomes visible.
        try:
            self.query_one("#tickets-list", ListView).focus()
        except Exception:
            pass

    def _on_descendant_focus(self, event: events.DescendantFocus) -> None:
        # Same tab-loop fix as NowScreen.
        event.stop()

    # --- daemon event handling ----------------------------------------------

    async def handle_snapshot(self, data: list[dict[str, Any]] | None) -> None:
        """Replace the entire state from a snapshot envelope."""
        if data is None:
            return
        self.tickets = {t["id"]: t for t in data}
        await self._rebuild_list()

    async def handle_event(self, kind: str, data: dict[str, Any]) -> None:
        """Apply a live tickets event."""
        # Bus-event shape: {ticket_id, work_type, status, ...} or wrapped
        # under a "ticket" key — handle both pragmatically.
        ticket_payload = data.get("ticket") if isinstance(data.get("ticket"), dict) else data
        ticket_id = ticket_payload.get("id") or data.get("ticket_id")
        if not ticket_id:
            return
        # If the event payload doesn't carry full ticket fields, we leave
        # the existing entry alone (snapshot will reconcile on reconnect).
        existing = self.tickets.get(ticket_id, {})
        merged = {**existing, **ticket_payload}
        if "id" not in merged:
            merged["id"] = ticket_id
        # Make a new dict so reactive sees the change
        new_state = dict(self.tickets)
        new_state[ticket_id] = merged
        self.tickets = new_state
        await self._rebuild_list()

    # --- rendering ----------------------------------------------------------

    def _sorted_tickets(self) -> list[dict[str, Any]]:
        """Sort by most-recent activity desc; tiebreak on id."""
        def keyfn(t: dict[str, Any]) -> tuple:
            ts = t.get("updated_at") or t.get("created_at") or ""
            return (ts, t.get("id", ""))

        return sorted(self.tickets.values(), key=keyfn, reverse=True)

    async def _rebuild_list(self) -> None:
        """Re-render the ListView from current state, preserving selection."""
        try:
            list_view = self.query_one("#tickets-list", ListView)
        except Exception:
            return  # not mounted yet
        # Capture currently selected id (if any) so we can re-select after rebuild
        selected_id = None
        if list_view.index is not None and 0 <= list_view.index < len(list_view.children):
            current = list_view.children[list_view.index]
            selected_id = getattr(current, "ticket_id", None)

        await list_view.clear()
        sorted_tickets = self._sorted_tickets()
        if sorted_tickets:
            await list_view.extend([self._build_list_item(t) for t in sorted_tickets])

        # Restore selection if the previously selected ticket still exists
        restore_index = 0
        if selected_id:
            for i, child in enumerate(list_view.children):
                if getattr(child, "ticket_id", None) == selected_id:
                    restore_index = i
                    break

        if list_view.children:
            list_view.index = restore_index

        # Update detail pane for the (possibly new) selection
        self._update_detail()

    def _build_list_item(self, ticket: dict[str, Any]) -> ListItem:
        status = ticket.get("status", "open")
        icon = _STATUS_ICON.get(status, "•")
        color = _STATUS_COLOR.get(status, "white")
        size = ticket.get("size", "?")
        title = ticket.get("title", "(untitled)")
        needs_answer = status == "needs_info"
        needs_marker = (
            "[#ff00ff bold][?][/#ff00ff bold] " if needs_answer else ""
        )
        rendered = (
            f"[{color}]{icon}[/{color}] "
            f"[dim][{size}][/dim] "
            f"{needs_marker}{title}"
        )
        item = ListItem(Static(rendered, markup=True))
        # Stash the ticket id so we can reverse-lookup on selection
        item.ticket_id = ticket.get("id")  # type: ignore[attr-defined]
        return item

    def _update_detail(self) -> None:
        try:
            detail = self.query_one("#tickets-detail", Static)
            list_view = self.query_one("#tickets-list", ListView)
        except Exception:
            return
        if list_view.index is None or list_view.index >= len(list_view.children):
            detail.update(
                "[dim]No ticket selected.[/dim]"
                if self.tickets
                else "[dim]No tickets yet.[/dim]"
            )
            return
        item = list_view.children[list_view.index]
        ticket_id = getattr(item, "ticket_id", None)
        if not ticket_id or ticket_id not in self.tickets:
            return
        ticket = self.tickets[ticket_id]
        detail.update(self._render_detail(ticket))

    def _render_detail(self, t: dict[str, Any]) -> str:
        status = t.get("status", "open")
        color = _STATUS_COLOR.get(status, "white")
        lines: list[str] = []
        lines.append(f"[bold]{t.get('title', '(untitled)')}[/bold]")
        lines.append("")
        lines.append(f"[{color}]●[/{color}] [bold]{status}[/bold]")
        lines.append(f"  type:  {t.get('work_type', '?')}")
        lines.append(f"  size:  {t.get('size', '?')}")
        lines.append(f"  id:    {t.get('id', '')}")
        if t.get("assignee"):
            lines.append(f"  assignee: {t['assignee']}")
        if t.get("parent_id"):
            lines.append(f"  parent: {t['parent_id']}")
        if t.get("blocked_by"):
            lines.append(f"  blocked by: {', '.join(t['blocked_by'])}")
        if t.get("workflow"):
            lines.append(f"  workflow: {t['workflow']}")
        if t.get("derived_from"):
            lines.append(f"  derived from: {t['derived_from']}")
        if t.get("description"):
            lines.append("")
            lines.append("[bold]Description[/bold]")
            lines.append(str(t["description"]))
        return "\n".join(lines)

    # --- selection handlers --------------------------------------------------

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        # User scrolled selection — refresh detail
        self._update_detail()

    def action_select_next(self) -> None:
        try:
            self.query_one("#tickets-list", ListView).action_cursor_down()
        except Exception:
            return

    def action_select_prev(self) -> None:
        try:
            self.query_one("#tickets-list", ListView).action_cursor_up()
        except Exception:
            return
