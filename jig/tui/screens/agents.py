"""Agents screen — grid dashboard of running agents.

Each currently-running (or recently-running) agent gets a compact card
in a responsive 3-column grid. Card border color signals health:

  - green  → active and making progress
  - yellow → active but quiet past the stuck threshold (no current tool)
  - grey   → inactive (phase finished)

Replaces the prior master/detail layout (left list + right detail
panel) which forced operators to click into one agent at a time —
unworkable for monitoring parallel work across many tickets.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from rich.markup import escape
from textual.app import ComposeResult
from textual.containers import Grid
from textual.css.query import NoMatches
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static


@dataclass
class AgentState:
    role: str
    ticket_id: str
    ticket_title: str
    phase: str | None
    started_at: float = field(default_factory=time.monotonic)
    elapsed: int = 0
    active: bool = True
    current_tool: str | None = None
    recent_tools: list[tuple[str, str]] = field(default_factory=list)
    allowed_tools: list[str] = field(default_factory=list)
    allowed_mcps: list[str] = field(default_factory=list)
    phase_prompt: str = ""
    # Wall-clock (monotonic) seconds at which ``current_tool`` last
    # cleared — i.e. when this agent last became idle. ``None`` means
    # the agent has not gone idle since its current_tool was set (or
    # since spawn). Used by the stuck heuristic to fire only when
    # idle time exceeds the threshold, not whenever total elapsed
    # lifetime does.
    idle_since: float | None = None


_ROLE_COLORS: dict[str, str] = {
    "pm": "magenta",
    "spec": "cyan",
    "test": "yellow",
    "implement": "green",
    "review": "blue",
    "validate": "white",
    "document": "dim white",
}


# Seconds of continuous idle time (no current tool in flight) before
# the card border flips yellow. Compared against wall-clock monotonic
# time, NOT the orchestrator's lifetime ``elapsed`` counter — using
# elapsed produced false positives during every normal between-tools
# pause for long-running agents.
_STUCK_THRESHOLD_S = 30


def _slug(key: str) -> str:
    """Sanitize an agent_key for use as a widget id (no ``:`` / ``.``)."""
    return key.replace(":", "-").replace(".", "-")


class _AgentCard(Widget):
    """Compact dashboard card for one running (or recently-running) agent.

    Border color is the health signal: green = active, yellow = stuck
    (active but no current tool past the stuck threshold), grey =
    inactive (phase finished). Card content shows role, ticket title,
    elapsed, current tool, and the last 3 tool calls — enough to scan
    parallel work and spot the outliers without drilling in.
    """

    DEFAULT_CSS = """
    _AgentCard {
        border: round $accent-darken-2;
        background: #131a28;
        padding: 0 1;
        height: 100%;
    }
    _AgentCard.stuck {
        border: round $warning;
    }
    _AgentCard.inactive {
        border: round $accent-darken-3;
        background: #0d1218;
    }
    _AgentCard #body {
        padding: 0;
    }
    """

    def __init__(self, agent: AgentState, agent_key: str) -> None:
        super().__init__(id=f"card-{_slug(agent_key)}")
        self.agent_key = agent_key
        self._agent = agent
        self._body = Static("", id="body", markup=True)

    def compose(self) -> ComposeResult:
        yield self._body

    def on_mount(self) -> None:
        self.refresh_card()

    def update_agent(self, agent: AgentState) -> None:
        self._agent = agent
        self.refresh_card()

    def _status(self) -> tuple[str, str]:
        """Return ``(css-class, dot-markup)`` per current health.

        Stuck = continuously idle (no current tool) for longer than
        ``_STUCK_THRESHOLD_S`` wall-clock seconds. ``idle_since`` is
        set in ``handle_agent_tool_result`` and cleared in
        ``handle_agent_tool``, so the moment the next tool fires the
        yellow indicator goes away.
        """
        if not self._agent.active:
            return "inactive", "[grey46]●[/grey46]"
        idle_since = self._agent.idle_since
        if (
            idle_since is not None
            and (time.monotonic() - idle_since) > _STUCK_THRESHOLD_S
        ):
            return "stuck", "[yellow]●[/yellow]"
        return "", "[green]●[/green]"

    def refresh_card(self) -> None:
        status_class, dot = self._status()
        for cls in ("stuck", "inactive"):
            if status_class == cls:
                self.add_class(cls)
            else:
                self.remove_class(cls)

        role_color = _ROLE_COLORS.get(self._agent.role, "white")
        title = self._agent.ticket_title or self._agent.ticket_id[:8]
        if len(title) > 28:
            title = title[:25] + "…"

        # Role and ticket fields come from external event data (ticket
        # titles, tool names, tool details) and are interpolated into
        # markup strings rendered with markup=True. Escape any literal
        # ``[`` / ``]`` to prevent a stray ``[/dim]`` in a ticket title
        # or tool detail from breaking surrounding markup.
        role_safe = escape(self._agent.role.upper())
        title_safe = escape(title)

        lines: list[str] = []
        lines.append(
            f"{dot} [{role_color} bold]{role_safe}[/{role_color} bold]"
            f"  [dim]{self._agent.elapsed}s[/dim]"
        )
        lines.append(f"[dim]ticket:[/dim] {title_safe}")
        lines.append("")
        if self._agent.current_tool:
            current_safe = escape(self._agent.current_tool[:38])
            lines.append(f"[cyan]▸[/cyan] {current_safe}")
        else:
            lines.append("[dim]▸ idle[/dim]")
        lines.append("")
        if self._agent.recent_tools:
            lines.append("[dim]recent:[/dim]")
            for tool, detail in self._agent.recent_tools[:3]:
                detail_trunc = detail[:24] + "…" if len(detail) > 24 else detail
                tool_safe = escape(tool[:12])
                detail_safe = escape(detail_trunc)
                lines.append(f"  [cyan]{tool_safe:<12}[/cyan] [dim]{detail_safe}[/dim]")
        else:
            lines.append("[dim]recent: (none yet)[/dim]")
        self._body.update("\n".join(lines))


class AgentsScreen(Widget):
    """Full-pane agents dashboard — grid of ``_AgentCard`` widgets, one
    per running (or recently-running) agent. Operator scans all parallel
    work at a glance; card border colour signals health (active / stuck
    / done). Replaces the prior list+detail layout that forced one-at-
    a-time deep dives.
    """

    DEFAULT_CSS = """
    AgentsScreen {
        border: round $accent;
        margin: 1 2;
        background: #1a2030;
    }
    AgentsScreen #empty-msg {
        padding: 4 2;
        color: $text-disabled;
    }
    AgentsScreen #agents-grid {
        grid-size: 3;
        grid-gutter: 1;
        padding: 1;
        height: 1fr;
    }
    """

    project_path: reactive[Path | None] = reactive(None)

    def __init__(self) -> None:
        super().__init__()
        self._agents: dict[str, AgentState] = {}  # "ticket_id:role" → state

    def set_project_path(self, path: Path | None) -> None:
        self.project_path = path

    def compose(self) -> ComposeResult:
        yield Static(
            "[dim]no agents have started yet[/dim]",
            id="empty-msg",
            markup=True,
        )
        yield Grid(id="agents-grid")

    def on_mount(self) -> None:
        # Initial visibility — empty placeholder shows, grid hides.
        # _rebuild_grid is the single source of truth for the toggle.
        self._rebuild_grid()

    # ── Event handlers ────────────────────────────────────────────────────

    def handle_agent_start(self, data: dict) -> None:
        role = data.get("role", "agent")
        ticket_id = data.get("ticket_id", "")
        ticket_title = data.get("ticket_title", "")
        phase = data.get("phase")
        agent_key = f"{ticket_id}:{role}" if ticket_id else role

        agent = AgentState(
            role=role,
            ticket_id=ticket_id,
            ticket_title=ticket_title,
            phase=phase,
        )
        self._load_role_config(agent)
        self._agents[agent_key] = agent
        self._rebuild_grid()

    def handle_agent_thinking(self, data: dict) -> None:
        role = data.get("role", "agent")
        ticket_id = data.get("ticket_id", "")
        agent_key = f"{ticket_id}:{role}" if ticket_id else role
        active = data.get("active", True)
        elapsed = int(data.get("elapsed", 0))
        if agent_key not in self._agents:
            return
        agent = self._agents[agent_key]
        agent.elapsed = elapsed
        agent.active = active
        self._rebuild_grid()

    def handle_agent_tool(self, data: dict) -> None:
        role = data.get("role", "agent")
        ticket_id = data.get("ticket_id", "")
        agent_key = f"{ticket_id}:{role}" if ticket_id else role
        tool = data.get("tool", "")
        detail = data.get("detail", "")
        if agent_key not in self._agents:
            return
        agent = self._agents[agent_key]
        agent.current_tool = f"{tool}  {detail[:50]}" if detail else tool
        agent.idle_since = None  # tool in flight — agent is busy
        agent.recent_tools.insert(0, (tool, detail))
        agent.recent_tools = agent.recent_tools[:10]
        self._rebuild_grid()

    def handle_agent_tool_result(self, data: dict) -> None:
        role = data.get("role", "agent")
        ticket_id = data.get("ticket_id", "")
        agent_key = f"{ticket_id}:{role}" if ticket_id else role
        if agent_key not in self._agents:
            return
        agent = self._agents[agent_key]
        agent.current_tool = None
        agent.idle_since = time.monotonic()  # start the idle clock
        self._rebuild_grid()

    # ── Internal helpers ──────────────────────────────────────────────────

    def _load_role_config(self, agent: AgentState) -> None:
        """Try to read role config from .jig/roles/<role>.yaml.

        ``load_role`` raises ``FileNotFoundError`` when the role file
        is absent (legit — not every project ships every role).
        Anything else (import errors, malformed YAML caught by
        pydantic, etc.) is a real bug and should surface, so the
        catch is narrow.
        """
        path = self.project_path
        if path is None:
            return
        from jig.persistence import load_role

        try:
            cfg = load_role(path, agent.role)
        except FileNotFoundError:
            return
        agent.allowed_tools = list(cfg.allowed_tools)
        agent.allowed_mcps = list(cfg.allowed_mcps)
        agent.phase_prompt = cfg.phase_prompt or ""

    def _rebuild_grid(self) -> None:
        """Sync the card grid to ``self._agents``: mount cards for new
        agents, update cards for existing ones, toggle the empty-state
        placeholder visibility. No card removal — ``self._agents`` is
        append-only (see inline comment)."""
        try:
            grid = self.query_one("#agents-grid", Grid)
            empty = self.query_one("#empty-msg", Static)
        except NoMatches:
            # Compose hasn't finished — early event from on_mount race.
            return

        empty.display = not self._agents
        grid.display = bool(self._agents)

        existing_cards: dict[str, _AgentCard] = {
            card.agent_key: card
            for card in grid.children
            if isinstance(card, _AgentCard)
        }

        # ``self._agents`` is append-only — once an agent appears it
        # stays (as a grey "inactive" card after its phase finishes)
        # so the operator can still see "this is what ran." No
        # removal pass needed today; revisit if/when agent eviction
        # becomes a thing.
        for agent_key, agent in self._agents.items():
            existing = existing_cards.get(agent_key)
            if existing is not None:
                existing.update_agent(agent)
            else:
                grid.mount(_AgentCard(agent, agent_key))
