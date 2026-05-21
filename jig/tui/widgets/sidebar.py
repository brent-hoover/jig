"""Right-docked Sidebar with four subzones (Activity / Needs You / Queue / Tail).

Per ``ontology.md`` "Sidebar" terminology. Visible regardless of which
Pane is active so the operator always has a peripheral view of:

  - Activity:  which agents are currently thinking / running
  - Needs You: tickets currently blocked on operator input
  - Queue:     which tickets are open / waiting
  - Tail:      the most recent bus events

Each subzone is fed by JigApp's ``_handle_daemon_message`` fan-out
when relevant snapshots / events arrive.
"""

from __future__ import annotations

from collections import deque
from typing import Any

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widget import Widget
from textual.widgets import Static

from jig.tui import ligature_safe


_HEADER_STYLE = "bold $accent on $boost"


class _Zone(Vertical):
    """A subzone of the Sidebar — a titled vertical block."""

    def __init__(self, *, zone_id: str, title: str, empty: str) -> None:
        super().__init__(id=zone_id)
        self._title = title
        self._empty = empty
        self._body = Static(f"[dim]{empty}[/dim]", id=f"{zone_id}-body", markup=True)

    def compose(self) -> ComposeResult:
        yield Static(
            f"[{_HEADER_STYLE}] {self._title} [/]", classes="zone-header", markup=True
        )
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
        max-height: 30;
        padding: 0 1 1 1;
        border-bottom: dashed $accent-darken-2;
    }
    Sidebar #needs-you {
        height: auto;
        min-height: 3;
        max-height: 10;
        padding: 0 1 1 1;
        background: #2a2418;
        border-bottom: dashed $warning;
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
        self._active_agents: dict[str, dict[str, Any]] = {}  # "ticket_id:role" → state
        self._agent_tools: dict[
            str, deque[tuple[str, str]]
        ] = {}  # "ticket_id:role" → [(tool, detail)]
        self._tickets: dict[str, dict[str, Any]] = {}
        self._events: list[dict[str, Any]] = []
        self._active_prompt: dict | None = None

    def compose(self) -> ComposeResult:
        with Vertical():
            yield _Zone(
                zone_id="activity",
                title="Activity",
                empty="no agents running",
            )
            yield _Zone(
                zone_id="needs-you",
                title="Needs You",
                empty="nothing waiting on you",
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
        """Add / refresh / remove an entry per agent from Activity."""
        role = data.get("role", "agent")
        ticket_id = data.get("ticket_id", "")
        agent_key = f"{ticket_id}:{role}" if ticket_id else role
        if not data.get("active", False):
            self._active_agents.pop(agent_key, None)
            self._agent_tools.pop(agent_key, None)
        else:
            self._active_agents[agent_key] = {
                "role": role,
                "elapsed": int(data.get("elapsed", 0)),
            }
        self._render_activity()

    def update_tool_use(self, data: dict) -> None:
        """Record a tool call under the agent's key for Activity display."""
        role = data.get("role", "agent")
        ticket_id = data.get("ticket_id", "")
        agent_key = f"{ticket_id}:{role}" if ticket_id else role
        tool = data.get("tool", "")
        detail = data.get("detail", "")
        if not tool:
            return
        if agent_key not in self._agent_tools:
            self._agent_tools[agent_key] = deque(maxlen=5)
        self._agent_tools[agent_key].appendleft((tool, detail))
        self._render_activity()

    def _render_activity(self) -> None:
        try:
            zone = self.query_one("#activity", _Zone)
        except Exception:
            return
        # Union of agents that are thinking AND agents with recent tool history.
        # Tool events may arrive without a matching thinking event (e.g. they
        # have a ticket_id in their key while the thinking event didn't), so we
        # show any key that has tools even if it's not currently thinking.
        all_keys: list[str] = list(
            dict.fromkeys(list(self._active_agents) + list(self._agent_tools))
        )
        if not all_keys:
            zone.set_lines([])
            return
        spinners = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        lines = []
        for agent_key in all_keys:
            state = self._active_agents.get(agent_key)
            # Derive the display role from the state dict or the key itself.
            role = (state or {}).get("role") or agent_key.split(":")[-1]
            if state:
                elapsed = state["elapsed"]
                spin = spinners[elapsed % len(spinners)]
                lines.append(
                    f"[dim]{spin}[/dim] [bold]{role}[/bold] [dim]({elapsed}s)[/dim]"
                )
            else:
                lines.append(f"  [bold]{role}[/bold]")
            for tool, detail in list(self._agent_tools.get(agent_key, [])):
                # Strip MCP namespace prefix: mcp__<ns>__<tool> → <tool>
                display_tool = tool
                if display_tool.startswith("mcp__") and display_tool.count("__") >= 2:
                    display_tool = display_tool.split("__", 2)[2]
                # Sidebar inner width 34. Format: "  ▸ {tool}: {detail}" — overhead 6 chars.
                if len(display_tool) > 14:
                    display_tool = display_tool[:13] + "…"
                avail = max(6, 34 - 6 - len(display_tool))
                detail_trunc = detail[:avail] + "…" if len(detail) > avail else detail
                lines.append(
                    f"  [dim]▸[/dim] [bold dim]{display_tool}[/bold dim][dim]: {detail_trunc}[/dim]"
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
        self._render_needs_you()

    def update_ticket_event(self, kind: str, data: dict) -> None:
        ticket_payload = (
            data.get("ticket") if isinstance(data.get("ticket"), dict) else data
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
        self._render_needs_you()

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

    def update_prompt(self, data: dict | None) -> None:
        """Called when a prompt becomes active (data) or is resolved (None)."""
        self._active_prompt = data
        self._render_needs_you()

    def _render_needs_you(self) -> None:
        """Render tickets currently blocked on operator input.

        Driven from the tickets snapshot — every ticket with status
        ``needs_info`` (or ``merge_conflict``, which also requires
        operator action) shows up here so the operator can see at a
        glance what's actually waiting on them.
        """
        try:
            zone = self.query_one("#needs-you", _Zone)
        except Exception:
            return
        actionable = [
            t
            for t in self._tickets.values()
            if t.get("status") in ("needs_info", "merge_conflict")
        ]
        # Sort: needs_info before merge_conflict (input is less destructive
        # than resolving a conflict, do the easy ones first).
        actionable.sort(key=lambda t: 0 if t.get("status") == "needs_info" else 1)
        lines = []
        if self._active_prompt:
            _TYPE_LABELS = {
                "brief_approval": "approve brief",
                "question_answer": "answer question",
                "init_complete": "confirm init",
                "direct_template": "fill template",
            }
            pt = self._active_prompt.get("prompt_type", "")
            label = _TYPE_LABELS.get(pt, pt.replace("_", " ") or "respond to prompt")
            lines.append(f"[bold magenta]▶[/bold magenta] {label}")
        for t in actionable[:6]:
            status = t.get("status", "")
            # Truncate before ligature_safe — ZWSPs inflate len().
            title = t.get("title", "(untitled)")
            if len(title) > 24:
                title = title[:21] + "…"
            title = ligature_safe(title)
            if status == "needs_info":
                lines.append(f"[bold yellow]?[/bold yellow] {title}")
            else:
                lines.append(f"[bold yellow]⚠[/bold yellow] {title}")
        zone.set_lines(lines)

    def _render_queue(self) -> None:
        try:
            zone = self.query_one("#queue", _Zone)
        except Exception:
            return
        # Open / in-flight tickets only (resolved/closed not interesting here)
        open_tickets = [
            t
            for t in self._tickets.values()
            if t.get("status", "open") in self._OPEN_STATUSES
        ]
        # Sort: needs_info first, then in_progress, then open, then blocked.
        order = {
            "needs_info": 0,
            "in_progress": 1,
            "open": 2,
            "blocked": 3,
            "merge_conflict": 4,
        }
        open_tickets.sort(key=lambda t: order.get(t.get("status", "open"), 99))
        lines = []
        for t in open_tickets[:10]:
            status = t.get("status", "open")
            glyph = self._STATUS_GLYPH.get(status, "•")
            color = self._STATUS_COLOR.get(status, "white")
            # Truncate before ligature_safe — ZWSPs inflate len().
            title = t.get("title", "(untitled)")
            if len(title) > 26:
                title = title[:23] + "…"
            title = ligature_safe(title)
            lines.append(f"[{color}]{glyph}[/] {title}")
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

    _KIND_LABELS: dict[str, str] = {
        "ticket_dispatched": "▶ dispatched",
        "ticket_completed": "✓ completed",
        "ticket_failed": "✗ failed",
        "ticket_merge_conflict": "⚡ merge conflict",
        "ticket_updated": "↻ updated",
        "ticket_created": "+ created",
        "comment_posted": "💬 comment",
        "phase_start": "▷ phase start",
        "phase_end": "▶ phase end",
        "agent_run": "✓ agent run",
        "scaffold_applied": "🏗 scaffold",
        "spec_generated": "📜 spec",
        "brief_approved": "✓ brief approved",
        "project_complete": "✅ project done",
        "analysis_complete": "📊 analysis done",
    }

    def _extract_kind(self, ev: dict) -> str:
        """Pick the most informative event-kind label from a heterogeneous
        event payload. Sidebar receives a mix of bus messages, typed
        envelopes, and bare ticket dicts — all with different shapes."""
        # Prefer explicit ``event_type`` on system events.
        for path in (
            ev.get("event_type"),
            ev.get("kind"),
            (ev.get("payload") or {}).get("kind"),
            (ev.get("payload") or {}).get("event_type"),
            (ev.get("data") or {}).get("kind"),
        ):
            if path and isinstance(path, str):
                return path
        # If we got a bare ticket dict, label by its status if present.
        status = ev.get("status") or (ev.get("payload") or {}).get("status")
        if isinstance(status, str) and status:
            return f"ticket → {status}"
        return ""

    def _extract_timestamp(self, ev: dict) -> str:
        """Find HH:MM:SS in any of the common timestamp paths."""
        for path in (
            ev.get("timestamp"),
            (ev.get("payload") or {}).get("timestamp"),
            (ev.get("data") or {}).get("timestamp"),
            ev.get("created_at"),
            ev.get("updated_at"),
        ):
            if isinstance(path, str) and len(path) >= 19:
                return path[11:19]
        return ""

    def _extract_subject(self, ev: dict) -> str:
        """A short subject (ticket title fragment, id, or role) so each
        tail line is meaningful at a glance.

        Order of preference:
        1. Inline ``title`` on the event payload (some envelopes carry
           it).
        2. Lookup the ticket id in our cached ``self._tickets`` dict
           (populated from the tickets snapshot) — gives us a title
           even when the event only carried the id.
        3. Short ticket id as the last resort (better than nothing).
        """
        for path in (
            ev.get("title"),
            (ev.get("data") or {}).get("title"),
            (ev.get("payload") or {}).get("title"),
        ):
            if isinstance(path, str) and path.strip():
                truncated = path[:24] + ("…" if len(path) > 24 else "")
                return ligature_safe(truncated)

        # Resolve via the cached ticket store.
        ticket_id = None
        for path in (
            ev.get("ticket_id"),
            (ev.get("data") or {}).get("ticket_id"),
            (ev.get("payload") or {}).get("ticket_id"),
            ev.get("id"),
        ):
            if isinstance(path, str) and path.strip():
                ticket_id = path
                break
        if ticket_id:
            t = self._tickets.get(ticket_id)
            if t and t.get("title"):
                title = t["title"]
                truncated = title[:24] + ("…" if len(title) > 24 else "")
                return ligature_safe(truncated)
            return ticket_id[:8]
        return ""

    def _render_tail(self) -> None:
        try:
            zone = self.query_one("#tail", _Zone)
        except Exception:
            return
        lines = []
        for ev in self._events[-8:]:
            ts = self._extract_timestamp(ev)
            raw_kind = self._extract_kind(ev)
            label = self._KIND_LABELS.get(raw_kind, raw_kind or "·")
            subject = self._extract_subject(ev)
            ts_part = f"[dim]{ts}[/dim] " if ts else ""
            # Sidebar inner width 34. Compute subject budget dynamically so
            # label + subject never exceeds one line.
            ts_visible = len(ts) + 1 if ts else 0
            if len(label) > 14:
                label = label[:13] + "…"
            avail_subject = max(0, 34 - ts_visible - len(label) - 1)
            if subject and len(subject) > avail_subject:
                subject = subject[:max(4, avail_subject - 1)] + "…"
            subject_part = f" [dim]{subject}[/dim]" if subject else ""
            lines.append(f"{ts_part}[cyan]{label}[/cyan]{subject_part}")
        zone.set_lines(lines)
