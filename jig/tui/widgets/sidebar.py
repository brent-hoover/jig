"""Right-docked Sidebar with three subzones (Activity / Queue / Tail).

Per ``ontology.md`` "Sidebar" terminology. Visible regardless of which
Pane is active so the operator always has a peripheral view of:

  - Activity: which agents are currently thinking / running
  - Queue:    which tickets are open / waiting
  - Tail:     the most recent bus events

Each subzone is fed by JigApp's ``_handle_daemon_message`` fan-out
when relevant snapshots / events arrive.
"""
from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widget import Widget
from textual.widgets import Static


_HEADER_STYLE = "bold $accent on $boost"


class _Zone(Vertical):
    """A subzone of the Sidebar — a titled vertical block."""

    def __init__(self, *, zone_id: str, title: str, empty: str) -> None:
        super().__init__(id=zone_id)
        self._title = title
        self._empty = empty
        self._body = Static(f"[dim]{empty}[/dim]", id=f"{zone_id}-body", markup=True)

    def compose(self) -> ComposeResult:
        yield Static(f"[{_HEADER_STYLE}] {self._title} [/]", classes="zone-header", markup=True)
        yield self._body

    def set_lines(self, lines: list[str]) -> None:
        if not lines:
            self._body.update(f"[dim]{self._empty}[/dim]")
            return
        self._body.update("\n".join(lines))


class Sidebar(Widget):
    """Right-docked persistent sidebar with three subzones."""

    DEFAULT_CSS = """
    Sidebar {
        width: 36;
        dock: right;
        background: #1a2030;
        border-left: solid $accent;
    }
    Sidebar > Vertical {
        height: 1fr;
    }
    Sidebar #activity {
        height: auto;
        min-height: 4;
        max-height: 12;
        padding: 0 1 1 1;
        border-bottom: dashed $accent-darken-2;
    }
    Sidebar #queue {
        height: 1fr;
        padding: 0 1 1 1;
        border-bottom: dashed $accent-darken-2;
    }
    Sidebar #tail {
        height: 12;
        padding: 0 1;
    }
    .zone-header {
        height: 1;
        margin-bottom: 0;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._active_agents: dict[str, dict[str, Any]] = {}  # role → state
        self._tickets: dict[str, dict[str, Any]] = {}
        self._events: list[dict[str, Any]] = []

    def compose(self) -> ComposeResult:
        with Vertical():
            yield _Zone(
                zone_id="activity",
                title="Activity",
                empty="no agents running",
            )
            yield _Zone(
                zone_id="queue",
                title="Tickets",
                empty="no open tickets",
            )
            yield _Zone(
                zone_id="tail",
                title="Recent",
                empty="no events yet",
            )

    # ---------------------------------------------------------------------
    # Activity — driven by agent_thinking events
    # ---------------------------------------------------------------------

    def update_thinking(self, data: dict) -> None:
        """Add / refresh / remove an entry per role from Activity."""
        role = data.get("role", "agent")
        if not data.get("active", False):
            self._active_agents.pop(role, None)
        else:
            self._active_agents[role] = {
                "role": role,
                "elapsed": int(data.get("elapsed", 0)),
            }
        self._render_activity()

    def _render_activity(self) -> None:
        try:
            zone = self.query_one("#activity", _Zone)
        except Exception:
            return
        if not self._active_agents:
            zone.set_lines([])
            return
        spinners = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        lines = []
        for role, state in self._active_agents.items():
            elapsed = state["elapsed"]
            spin = spinners[elapsed % len(spinners)]
            lines.append(
                f"[dim]{spin}[/dim] [bold]{role}[/bold] [dim]({elapsed}s)[/dim]"
            )
        zone.set_lines(lines)

    # ---------------------------------------------------------------------
    # Queue — driven by tickets snapshot + tickets/created/updated events
    # ---------------------------------------------------------------------

    def update_tickets_snapshot(self, data: list[dict] | None) -> None:
        if data is None:
            self._tickets = {}
        else:
            self._tickets = {t["id"]: t for t in data if "id" in t}
        self._render_queue()

    def update_ticket_event(self, kind: str, data: dict) -> None:
        ticket_payload = (
            data.get("ticket")
            if isinstance(data.get("ticket"), dict)
            else data
        )
        ticket_id = ticket_payload.get("id") or data.get("ticket_id")
        if not ticket_id:
            return
        existing = self._tickets.get(ticket_id, {})
        merged = {**existing, **ticket_payload}
        if "id" not in merged:
            merged["id"] = ticket_id
        self._tickets[ticket_id] = merged
        self._render_queue()

    _STATUS_GLYPH = {
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
    _OPEN_STATUSES = frozenset(
        {"open", "in_progress", "blocked", "needs_info", "merge_conflict"}
    )

    def _render_queue(self) -> None:
        try:
            zone = self.query_one("#queue", _Zone)
        except Exception:
            return
        # Open / in-flight tickets only (resolved/closed not interesting here)
        open_tickets = [
            t for t in self._tickets.values()
            if t.get("status", "open") in self._OPEN_STATUSES
        ]
        # Sort: needs_info first, then in_progress, then open, then blocked.
        order = {"needs_info": 0, "in_progress": 1, "open": 2, "blocked": 3, "merge_conflict": 4}
        open_tickets.sort(key=lambda t: order.get(t.get("status", "open"), 99))
        lines = []
        for t in open_tickets[:10]:
            status = t.get("status", "open")
            glyph = self._STATUS_GLYPH.get(status, "•")
            color = self._STATUS_COLOR.get(status, "white")
            title = t.get("title", "(untitled)")
            if len(title) > 26:
                title = title[:23] + "…"
            lines.append(f"[{color}]{glyph}[/{color}] {title}")
        zone.set_lines(lines)

    # ---------------------------------------------------------------------
    # Tail — driven by events snapshot + tickets-related events
    # ---------------------------------------------------------------------

    def update_events_snapshot(self, data: list[dict] | None) -> None:
        if data is None:
            self._events = []
        else:
            self._events = list(data)
        self._render_tail()

    def append_event(self, ev: dict) -> None:
        self._events.append(ev)
        # Keep only the last 50 in-memory; only render the last ~8.
        if len(self._events) > 50:
            del self._events[: len(self._events) - 50]
        self._render_tail()

    def _render_tail(self) -> None:
        try:
            zone = self.query_one("#tail", _Zone)
        except Exception:
            return
        lines = []
        for ev in self._events[-8:]:
            ts = (ev.get("timestamp") or "")[11:19] or "??:??:??"
            kind = (ev.get("payload") or {}).get("kind") or ev.get("type") or "?"
            if len(kind) > 18:
                kind = kind[:15] + "…"
            lines.append(f"[dim]{ts}[/dim] [cyan]{kind}[/cyan]")
        zone.set_lines(lines)
