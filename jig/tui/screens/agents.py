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


_ROLE_COLORS: dict[str, str] = {
    "pm": "magenta",
    "spec": "cyan",
    "test": "yellow",
    "implement": "green",
    "review": "blue",
    "validate": "white",
    "document": "dim white",
}


# An agent whose ``elapsed`` (orchestrator thinking-event counter)
# hasn't advanced past this many seconds without a current tool call
# is treated as stuck for dashboard purposes — card border goes yellow.
# Future iteration could compare last-update wall-clock to now rather
# than relying on the counter; this is an adequate first cut.
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
        """Return ``(css-class, dot-markup)`` per current health."""
        if not self._agent.active:
            return "inactive", "[grey46]●[/grey46]"
        if (
            self._agent.elapsed > _STUCK_THRESHOLD_S
            and self._agent.current_tool is None
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

        lines: list[str] = []
        lines.append(
            f"{dot} [{role_color} bold]{self._agent.role.upper()}[/{role_color} bold]"
            f"  [dim]{self._agent.elapsed}s[/dim]"
        )
        lines.append(f"[dim]ticket:[/dim] {title}")
        lines.append("")
        if self._agent.current_tool:
            lines.append(f"[$accent]▸[/$accent] {self._agent.current_tool[:38]}")
        else:
            lines.append("[dim]▸ idle[/dim]")
        lines.append("")
        if self._agent.recent_tools:
            lines.append("[dim]recent:[/dim]")
            for tool, detail in self._agent.recent_tools[:3]:
                detail_trunc = detail[:24] + "…" if len(detail) > 24 else detail
                lines.append(
                    f"  [cyan]{tool[:12]:<12}[/cyan] [dim]{detail_trunc}[/dim]"
                )
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
        agent.recent_tools.insert(0, (tool, detail))
        agent.recent_tools = agent.recent_tools[:10]
        self._rebuild_grid()

    def handle_agent_tool_result(self, data: dict) -> None:
        role = data.get("role", "agent")
        ticket_id = data.get("ticket_id", "")
        agent_key = f"{ticket_id}:{role}" if ticket_id else role
        if agent_key in self._agents:
            self._agents[agent_key].current_tool = None
        self._rebuild_grid()

    # ── Internal helpers ──────────────────────────────────────────────────

    def _load_role_config(self, agent: AgentState) -> None:
        """Try to read role config from .jig/roles/<role>.yaml."""
        path = self.project_path
        if path is None:
            return
        try:
            from jig.persistence import load_role

            cfg = load_role(path, agent.role)
            agent.allowed_tools = list(cfg.allowed_tools)
            agent.allowed_mcps = list(cfg.allowed_mcps)
            agent.phase_prompt = cfg.phase_prompt or ""
        except Exception:
            pass

    def _rebuild_grid(self) -> None:
        """Sync the card grid to ``self._agents``: mount cards for new
        agents, update cards for existing ones, remove cards for departed
        ones, toggle the empty-state placeholder visibility."""
        try:
            grid = self.query_one("#agents-grid", Grid)
            empty = self.query_one("#empty-msg", Static)
        except NoMatches:
            # Compose hasn't finished — early event from on_mount race.
            return

        empty.display = not self._agents
        grid.display = bool(self._agents)

        new_keys = set(self._agents.keys())
        existing_cards: dict[str, _AgentCard] = {
            card.agent_key: card
            for card in grid.children
            if isinstance(card, _AgentCard)
        }

        # Remove stale cards.
        for card_key, card in existing_cards.items():
            if card_key not in new_keys:
                card.remove()

        # Update existing, mount new.
        for agent_key, agent in self._agents.items():
            existing = existing_cards.get(agent_key)
            if existing is not None:
                existing.update_agent(agent)
            else:
                grid.mount(_AgentCard(agent, agent_key))
