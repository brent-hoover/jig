"""Agents screen — live view of running agents and their configuration."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import ScrollableContainer, Vertical
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Label, ListItem, ListView, Static


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


class _AgentListItem(ListItem):
    def __init__(self, agent: AgentState) -> None:
        super().__init__()
        self.agent_key = agent.role  # stable key
        self._agent = agent

    def compose(self) -> ComposeResult:
        color = _ROLE_COLORS.get(self._agent.role, "white")
        spinners = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        spin = spinners[self._agent.elapsed % len(spinners)] if self._agent.active else "·"
        title = self._agent.ticket_title or self._agent.ticket_id[:8]
        if len(title) > 28:
            title = title[:25] + "…"
        yield Static(
            f"[{color}]{spin} {self._agent.role}[/{color}] [dim]{title}[/dim]",
            markup=True,
        )


class _DetailPanel(Widget):
    """Right panel showing full agent details."""

    DEFAULT_CSS = """
    _DetailPanel {
        height: 1fr;
        padding: 1 2;
    }
    _DetailPanel .section-header {
        color: $accent;
        text-style: bold;
        margin-top: 1;
    }
    _DetailPanel .prompt-text {
        color: $text-muted;
        margin-left: 2;
    }
    _DetailPanel #no-agent {
        color: $text-disabled;
        margin: 4 0 0 2;
    }
    """

    def compose(self) -> ComposeResult:
        yield ScrollableContainer(
            Static("", id="detail-content", markup=True),
            id="detail-scroll",
        )

    def show_agent(self, agent: AgentState | None) -> None:
        try:
            content = self.query_one("#detail-content", Static)
        except Exception:
            return
        if agent is None:
            content.update("[dim]no agent selected[/dim]")
            return
        color = _ROLE_COLORS.get(agent.role, "white")
        spinners = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        spin = spinners[agent.elapsed % len(spinners)] if agent.active else "·"
        phase_str = f"  [dim]phase:[/dim] {agent.phase}" if agent.phase else ""

        lines: list[str] = []

        # ── Header ─────────────────────────────────────────────────────────
        lines.append(
            f"[{color} bold]{spin} {agent.role.upper()}[/{color} bold]"
            f"  [dim]({agent.elapsed}s)[/dim]{phase_str}"
        )
        lines.append(f"[dim]ticket:[/dim] {agent.ticket_title or agent.ticket_id[:8]}")
        lines.append(f"[dim]id:[/dim] [dim]{agent.ticket_id[:16]}[/dim]")

        # ── Current tool ───────────────────────────────────────────────────
        if agent.current_tool:
            lines.append("")
            lines.append("[bold $accent]▸ Current tool[/bold $accent]")
            lines.append(f"  {agent.current_tool}")

        # ── Recent tools ───────────────────────────────────────────────────
        if agent.recent_tools:
            lines.append("")
            lines.append("[bold $accent]Recent tools[/bold $accent]")
            for tool, detail in agent.recent_tools[:8]:
                detail_trunc = detail[:60] + "…" if len(detail) > 60 else detail
                lines.append(f"  [cyan]{tool:<14}[/cyan] [dim]{detail_trunc}[/dim]")

        # ── Allowed tools ──────────────────────────────────────────────────
        lines.append("")
        lines.append("[bold $accent]Allowed tools[/bold $accent]")
        if agent.allowed_tools:
            # wrap into rows of ~4
            rows = [agent.allowed_tools[i:i+4] for i in range(0, len(agent.allowed_tools), 4)]
            for row in rows:
                lines.append("  " + "  ".join(f"[green]{t}[/green]" for t in row))
        else:
            lines.append("  [dim](all tools)[/dim]")

        # ── Skills / MCPs ──────────────────────────────────────────────────
        lines.append("")
        lines.append("[bold $accent]Skills / MCPs[/bold $accent]")
        if agent.allowed_mcps:
            for mcp in agent.allowed_mcps:
                lines.append(f"  [magenta]◆[/magenta] {mcp}")
        else:
            lines.append("  [dim]none[/dim]")

        # ── Phase prompt ───────────────────────────────────────────────────
        lines.append("")
        lines.append("[bold $accent]Phase prompt[/bold $accent]")
        if agent.phase_prompt:
            # Show first 800 chars so the panel doesn't become a wall of text
            prompt_preview = agent.phase_prompt.strip()[:800]
            if len(agent.phase_prompt.strip()) > 800:
                prompt_preview += "\n[dim]… (truncated)[/dim]"
            for ln in prompt_preview.splitlines():
                lines.append(f"  [dim]{ln}[/dim]")
        else:
            lines.append("  [dim](not available)[/dim]")

        content.update("\n".join(lines))


class AgentsScreen(Widget):
    """Full-pane agents view: list on the left, detail on the right."""

    DEFAULT_CSS = """
    AgentsScreen {
        border: round $accent;
        margin: 1 2;
        background: #1a2030;
        layout: horizontal;
    }
    AgentsScreen #agents-list-pane {
        width: 32;
        border-right: solid $accent-darken-2;
        padding: 0 1;
    }
    AgentsScreen #agents-list-pane Label {
        color: $accent;
        text-style: bold;
        padding: 0 0 1 0;
    }
    AgentsScreen #agents-list {
        height: 1fr;
        background: #1a2030;
    }
    AgentsScreen #agents-detail-pane {
        width: 1fr;
    }
    """

    project_path: reactive[Path | None] = reactive(None)

    def __init__(self) -> None:
        super().__init__()
        self._agents: dict[str, AgentState] = {}  # role → state
        self._selected_role: str | None = None

    def set_project_path(self, path: Path | None) -> None:
        self.project_path = path

    def compose(self) -> ComposeResult:
        with Vertical(id="agents-list-pane"):
            yield Label("Agents")
            yield ListView(id="agents-list")
        yield _DetailPanel(id="agents-detail-pane")

    # ── Event handlers ────────────────────────────────────────────────────

    def handle_agent_start(self, data: dict) -> None:
        role = data.get("role", "agent")
        ticket_id = data.get("ticket_id", "")
        ticket_title = data.get("ticket_title", "")
        phase = data.get("phase")

        agent = AgentState(
            role=role,
            ticket_id=ticket_id,
            ticket_title=ticket_title,
            phase=phase,
        )
        self._load_role_config(agent)
        self._agents[role] = agent
        self._selected_role = role
        self._rebuild_list()
        self._refresh_detail()

    def handle_agent_thinking(self, data: dict) -> None:
        role = data.get("role", "agent")
        active = data.get("active", True)
        elapsed = int(data.get("elapsed", 0))
        if role not in self._agents:
            return
        agent = self._agents[role]
        agent.elapsed = elapsed
        agent.active = active
        if not active:
            # Keep the agent visible briefly after it finishes so the
            # operator can read the detail, but mark it done.
            pass
        self._rebuild_list()
        if self._selected_role == role:
            self._refresh_detail()

    def handle_agent_tool(self, data: dict) -> None:
        role = data.get("role", "agent")
        tool = data.get("tool", "")
        detail = data.get("detail", "")
        if role not in self._agents:
            return
        agent = self._agents[role]
        agent.current_tool = f"{tool}  {detail[:50]}" if detail else tool
        agent.recent_tools.insert(0, (tool, detail))
        agent.recent_tools = agent.recent_tools[:10]
        if self._selected_role == role:
            self._refresh_detail()

    def handle_agent_tool_result(self, data: dict) -> None:
        role = data.get("role", "agent")
        if role in self._agents:
            self._agents[role].current_tool = None
        if self._selected_role == role:
            self._refresh_detail()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if isinstance(item, _AgentListItem):
            self._selected_role = item.agent_key
            self._refresh_detail()

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

    def _rebuild_list(self) -> None:
        try:
            lv = self.query_one("#agents-list", ListView)
        except Exception:
            return
        # Keep only active agents in the list; inactive ones fade out after
        # the next cycle. For now show all agents we've seen this session.
        existing_keys = {
            item.agent_key
            for item in lv.children
            if isinstance(item, _AgentListItem)
        }
        agents_to_show = list(self._agents.values())
        new_keys = {a.role for a in agents_to_show}

        # Remove stale
        for item in list(lv.children):
            if isinstance(item, _AgentListItem) and item.agent_key not in new_keys:
                item.remove()

        # Update or add
        for agent in agents_to_show:
            if agent.role in existing_keys:
                # Refresh the static inside the existing item
                for item in lv.children:
                    if isinstance(item, _AgentListItem) and item.agent_key == agent.role:
                        item._agent = agent
                        try:
                            label = item.query_one(Static)
                            color = _ROLE_COLORS.get(agent.role, "white")
                            spinners = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
                            spin = spinners[agent.elapsed % len(spinners)] if agent.active else "·"
                            title = agent.ticket_title or agent.ticket_id[:8]
                            if len(title) > 28:
                                title = title[:25] + "…"
                            label.update(
                                f"[{color}]{spin} {agent.role}[/{color}] [dim]{title}[/dim]"
                            )
                        except Exception:
                            pass
            else:
                lv.append(_AgentListItem(agent))

    def _refresh_detail(self) -> None:
        try:
            panel = self.query_one(_DetailPanel)
        except Exception:
            return
        agent = self._agents.get(self._selected_role or "") if self._selected_role else None
        if agent is None and self._agents:
            agent = next(iter(self._agents.values()))
        panel.show_agent(agent)
