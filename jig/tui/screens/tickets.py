"""Tickets screen — master/detail of all tickets, live-updated.

Left pane: scrollable list of tickets sorted by most recent activity
(falls back to creation order if no activity timestamp). Right pane:
detail view of the focused ticket (status, work type, size, title,
description, assignee, parent_id, blocked_by, workflow).

Hotkeys (Tickets pane only):
  j / down       move selection down
  k / up         move selection up
  b              toggle list / board view
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
from textual.containers import Container, Horizontal, ScrollableContainer, Vertical
from textual.reactive import reactive
from textual.widgets import ListItem, ListView, Static

from jig.tui import ligature_safe


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

_BOARD_COLUMNS = [
    ("open", "Open", "#888888"),
    ("in_progress", "In Progress", "#ffcc00"),
    ("needs_info", "Needs Info", "#ff00ff"),
    ("blocked", "Blocked", "#ff8800"),  # also catches merge_conflict
    ("failed", "Failed", "#cc0000"),
    ("resolved", "Resolved", "#00cc00"),
    ("closed", "Closed", "#444444"),
]


class BoardView(ScrollableContainer):
    """Kanban-style status columns. One column per ticket status."""

    DEFAULT_CSS = """
    BoardView {
        width: 1fr;
        height: 1fr;
    }
    BoardView > Horizontal {
        height: auto;
    }
    .board-column {
        width: 30;
        margin: 0 1;
        border: solid $accent;
        padding: 0 1;
        height: auto;
    }
    .board-column-header {
        height: 1;
        text-style: bold;
    }
    .board-card {
        margin: 0 0 1 0;
    }
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._tickets: list[dict[str, Any]] = []

    def compose(self) -> ComposeResult:
        with Horizontal():
            for status_key, label, color in _BOARD_COLUMNS:
                with Vertical(classes="board-column", id=f"col-{status_key}"):
                    yield Static(
                        f"[{color}]{label}[/{color}] [dim](0)[/dim]",
                        classes="board-column-header",
                        id=f"hdr-{status_key}",
                        markup=True,
                    )
                    yield Vertical(id=f"cards-{status_key}")

    def update_tickets(self, tickets: list[dict[str, Any]]) -> None:
        self._tickets = tickets
        # Re-bucket
        buckets: dict[str, list[dict[str, Any]]] = {k: [] for k, _, _ in _BOARD_COLUMNS}
        for t in tickets:
            status = t.get("status", "open")
            # Group merge_conflict under "blocked"
            if status == "merge_conflict":
                status = "blocked"
            if status in buckets:
                buckets[status].append(t)
        # Repaint each column
        for status_key, label, color in _BOARD_COLUMNS:
            try:
                hdr = self.query_one(f"#hdr-{status_key}", Static)
                cards = self.query_one(f"#cards-{status_key}", Vertical)
            except Exception:
                continue
            count = len(buckets[status_key])
            hdr.update(f"[{color}]{label}[/{color}] [dim]({count})[/dim]")
            # Clear and re-mount cards
            cards.remove_children()
            for t in buckets[status_key]:
                cards.mount(self._build_card(t, color))

    def _build_card(self, t: dict[str, Any], color: str) -> Static:
        status = t.get("status", "open")
        icon = _STATUS_ICON.get(status, "•")
        size = t.get("size", "?")
        # Truncate BEFORE ligature_safe — ZWSPs inflate len() but not
        # visual width, so combining the two would either over-truncate
        # or cut across a ZWSP position.
        title = t.get("title", "(untitled)")
        if len(title) > 24:
            title = title[:21] + "…"
        title = ligature_safe(title)
        return Static(
            f"[{color}]{icon}[/{color}] [dim][{size}][/dim] {title}",
            classes="board-card",
            markup=True,
        )


class TicketsScreen(Container):
    """Live master/detail of all tickets."""

    BINDINGS = [
        Binding("j", "select_next", "Down", show=False),
        Binding("k", "select_prev", "Up", show=False),
        Binding("b", "toggle_view", "List/Board"),
    ]

    DEFAULT_CSS = """
    TicketsScreen {
        layout: vertical;
        height: 1fr;
    }
    TicketsScreen > #list-mode-row {
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
    view_mode: reactive[str] = reactive("list", recompose=False)

    def compose(self) -> ComposeResult:
        with Horizontal(id="list-mode-row"):
            yield ListView(id="tickets-list")
            yield Static(
                "[dim]waiting for tickets…[/dim]",
                id="tickets-detail",
                markup=True,
            )
        yield BoardView(id="board-mode-view")

    def on_mount(self) -> None:
        # Hide board initially
        self.query_one("#board-mode-view").display = False

    def on_show(self) -> None:
        # Focus the ListView when the pane becomes visible (only in list mode).
        if self.view_mode == "list":
            try:
                self.query_one("#tickets-list", ListView).focus()
            except Exception:
                pass

    def _on_descendant_focus(self, event: events.DescendantFocus) -> None:
        # Same tab-loop fix as NowScreen.
        event.stop()

    # --- view mode ----------------------------------------------------------

    def watch_view_mode(self, mode: str) -> None:
        list_row = self.query_one("#list-mode-row")
        board = self.query_one("#board-mode-view", BoardView)
        if mode == "list":
            list_row.display = True
            board.display = False
        else:
            list_row.display = False
            board.display = True
            board.update_tickets(list(self.tickets.values()))

    def action_toggle_view(self) -> None:
        self.view_mode = "board" if self.view_mode == "list" else "list"

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
        ticket_payload = (
            data.get("ticket") if isinstance(data.get("ticket"), dict) else data
        )
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
        if list_view.index is not None and 0 <= list_view.index < len(
            list_view.children
        ):
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

        # Keep board in sync if it's visible
        if self.view_mode == "board":
            try:
                board = self.query_one("#board-mode-view", BoardView)
                board.update_tickets(list(self.tickets.values()))
            except Exception:
                pass

    def _build_list_item(self, ticket: dict[str, Any]) -> ListItem:
        status = ticket.get("status", "open")
        icon = _STATUS_ICON.get(status, "•")
        color = _STATUS_COLOR.get(status, "white")
        size = ticket.get("size", "?")
        title = ligature_safe(ticket.get("title", "(untitled)"))  # no truncation here
        needs_answer = status == "needs_info"
        needs_marker = "[#ff00ff bold][?][/#ff00ff bold] " if needs_answer else ""
        rendered = (
            f"[{color}]{icon}[/{color}] [dim][{size}][/dim] {needs_marker}{title}"
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
        lines.append(f"[bold]{ligature_safe(t.get('title', '(untitled)'))}[/bold]")
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
        findings = t.get("findings") or []
        if findings:
            lines.append("")
            lines.append("[bold]Findings[/bold]")
            for f in findings:
                fid = f.get("finding_id", "?")
                loc = f.get("file") or "(diff-wide)"
                line_no = f.get("line")
                loc_full = f"{loc}:{line_no}" if line_no else loc
                severity = f.get("severity", "?")
                status = f.get("status", "open")
                # Color the status independently of the ticket palette.
                status_color = {
                    "open": "#888888",
                    "addressed": "#ffcc00",
                    "resolved": "#00cc00",
                    "reraised": "#ff8800",
                }.get(status, "white")
                lines.append(
                    f"  [{fid}] {loc_full} — {severity} — "
                    f"[{status_color}]{status}[/{status_color}]"
                )
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
