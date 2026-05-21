"""Events screen — scrollable bus event tail.

Renders the most recent N bus messages from the daemon's snapshot.
Each row is one event line; selecting one and pressing Enter pops a
modal with the full payload as JSON.

Hotkeys (Events pane only — bound on JigApp with pane guards):
  f         cycle filter (all → ticket_* → comment_* → agent_* → all)
  F         toggle follow mode (auto-scroll-to-bottom on new events)
  enter     open detail modal on selected event

Live updates: v0 relies on snapshot-on-subscribe only; reconnect to
refresh. Live event streaming for this topic is a future enhancement.
"""

from __future__ import annotations

from typing import Any

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.reactive import reactive
from textual.widgets import ListItem, ListView, Static


_FILTER_CYCLE = [
    ("all", lambda kind: True),
    ("ticket_*", lambda kind: kind and kind.startswith("ticket_")),
    ("comment_*", lambda kind: kind and kind.startswith("comment_")),
    ("agent_*", lambda kind: kind and kind.startswith("agent_")),
    ("scaffold_*", lambda kind: kind and kind.startswith("scaffold_")),
]


class EventsScreen(Container):
    """Live (snapshot-driven for v0) bus-event tail."""

    BINDINGS = [
        Binding("j", "select_next", "Down", show=False),
        Binding("k", "select_prev", "Up", show=False),
    ]

    DEFAULT_CSS = """
    EventsScreen {
        layout: vertical;
        height: 1fr;
    }
    #events-status {
        height: 1;
        background: $panel;
        padding: 0 1;
    }
    #events-list {
        height: 1fr;
    }
    """

    events_data: reactive[list[dict[str, Any]]] = reactive(list, recompose=False)
    filter_idx: reactive[int] = reactive(0, recompose=False)
    follow: reactive[bool] = reactive(True, recompose=False)

    def compose(self) -> ComposeResult:
        yield Static(self._status_text(), id="events-status", markup=True)
        yield ListView(id="events-list")

    def on_show(self) -> None:
        try:
            self.query_one("#events-list", ListView).focus()
        except Exception:
            pass

    def _on_descendant_focus(self, event: events.DescendantFocus) -> None:
        event.stop()

    # --- daemon events -------------------------------------------------------

    async def handle_snapshot(self, data: list[dict[str, Any]] | None) -> None:
        self.events_data = list(data or [])
        await self._rebuild_list()

    # --- rendering -----------------------------------------------------------

    def _status_text(self) -> str:
        flt_label, _ = _FILTER_CYCLE[self.filter_idx]
        follow_label = "[green]ON[/green]" if self.follow else "[dim]OFF[/dim]"
        return (
            f"[bold]Events[/bold]  "
            f"[dim]filter:[/dim] {flt_label}  "
            f"[dim]follow:[/dim] {follow_label}  "
            f"[dim]({len(self._filtered())}/{len(self.events_data)})[/dim]"
        )

    def _event_kind(self, ev: dict[str, Any]) -> str:
        """Extract the event kind from either snapshot or live-event shape."""
        return (
            ev.get("event_type")
            or (ev.get("payload") or {}).get("kind")
            or ev.get("kind")
            or ""
        )

    def _filtered(self) -> list[dict[str, Any]]:
        _, predicate = _FILTER_CYCLE[self.filter_idx]
        return [ev for ev in self.events_data if predicate(self._event_kind(ev))]

    async def _rebuild_list(self) -> None:
        try:
            list_view = self.query_one("#events-list", ListView)
            status = self.query_one("#events-status", Static)
        except Exception:
            return
        await list_view.clear()
        for ev in self._filtered():
            list_view.append(self._build_row(ev))
        status.update(self._status_text())
        # Follow mode: scroll to last
        if self.follow and len(list_view.children) > 0:
            list_view.index = len(list_view.children) - 1
            list_view.scroll_end(animate=False)

    async def append_live_event(self, ev: dict[str, Any]) -> None:
        """Append a single live event and re-render (called from JigApp fan-out)."""
        self.events_data = [*self.events_data, ev]
        if len(self.events_data) > 500:
            self.events_data = self.events_data[-500:]
        await self._rebuild_list()

    def _build_row(self, ev: dict[str, Any]) -> ListItem:
        ts = (ev.get("timestamp") or "")[:19]  # trim sub-seconds
        kind = self._event_kind(ev) or ev.get("type") or "?"
        sender = ev.get("from") or ev.get("sender") or "?"
        topic = ev.get("topic") or ""
        # Pluck a useful detail if present (ticket_id, comment_id, etc.)
        payload = ev.get("payload") or {}
        detail_keys = ["ticket_id", "comment_id", "phase", "role"]
        details = []
        for k in detail_keys:
            if k in payload:
                details.append(f"{k}={payload[k]}")
        detail_str = "  " + " ".join(details) if details else ""
        rendered = (
            f"[dim]{ts}[/dim]  "
            f"[cyan]{kind}[/cyan]  "
            f"[dim]{sender}→{topic}[/dim]"
            f"{detail_str}"
        )
        item = ListItem(Static(rendered, markup=True))
        item.event_data = ev  # type: ignore[attr-defined]
        return item

    # --- actions -------------------------------------------------------------

    async def cycle_filter(self) -> None:
        self.filter_idx = (self.filter_idx + 1) % len(_FILTER_CYCLE)
        await self._rebuild_list()

    async def toggle_follow(self) -> None:
        self.follow = not self.follow
        try:
            status = self.query_one("#events-status", Static)
            status.update(self._status_text())
        except Exception:
            return

    def get_selected_event(self) -> dict[str, Any] | None:
        try:
            list_view = self.query_one("#events-list", ListView)
        except Exception:
            return None
        if list_view.index is None or list_view.index >= len(list_view.children):
            return None
        item = list_view.children[list_view.index]
        return getattr(item, "event_data", None)

    def action_select_next(self) -> None:
        try:
            self.query_one("#events-list", ListView).action_cursor_down()
        except Exception:
            return

    def action_select_prev(self) -> None:
        try:
            self.query_one("#events-list", ListView).action_cursor_up()
        except Exception:
            return
