"""Spec screen — capabilities (grouped by state) + non-goals.

Master pane: Tree of capabilities grouped by state, plus a non-goals branch.
Detail pane: focused capability or non-goal rendered in full.

Modals:
  r  raw YAML view of the structured spec
  b  rendered brief markdown

Live data:
  - On subscribe: snapshot envelope arrives with the full StructuredSpec
    (or None if no spec exists yet)
  - No event-driven update for v0 — re-subscribe via reconnect to refresh
"""

from __future__ import annotations

from typing import Any

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.reactive import reactive
from textual.widgets import Static, Tree


_STATE_GROUPS = [
    ("backlog", "Backlog", "#888888"),
    ("planned_uncommitted", "Planned (uncommitted)", "#5588aa"),
    ("planned", "Planned", "#00aaff"),
    ("in_progress", "In Progress", "#ffcc00"),
    ("built", "Built", "#00cc00"),
    ("archived", "Archived", "#444444"),
]


class SpecScreen(Container):
    """Live capabilities + non-goals view."""

    BINDINGS = [
        Binding("j", "select_next", "Down", show=False),
        Binding("k", "select_prev", "Up", show=False),
    ]

    DEFAULT_CSS = """
    SpecScreen {
        layout: vertical;
        height: 1fr;
    }
    SpecScreen > Horizontal {
        height: 1fr;
    }
    #spec-tree {
        width: 32;
        border-right: solid $accent;
    }
    #spec-detail {
        padding: 1 2;
        height: 1fr;
    }
    """

    spec: reactive[dict | None] = reactive(None, recompose=False)

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield Tree("Spec", id="spec-tree")
            yield Static("[dim]waiting for spec…[/dim]", id="spec-detail", markup=True)

    def on_show(self) -> None:
        try:
            self.query_one("#spec-tree", Tree).focus()
        except Exception:
            pass

    def _on_descendant_focus(self, event: events.DescendantFocus) -> None:
        event.stop()

    # --- daemon events -------------------------------------------------------

    async def handle_snapshot(self, data: dict | None) -> None:
        self.spec = data
        await self._rebuild_tree()

    # --- rendering -----------------------------------------------------------

    async def _rebuild_tree(self) -> None:
        try:
            tree = self.query_one("#spec-tree", Tree)
        except Exception:
            return
        tree.clear()
        spec = self.spec
        if not spec:
            tree.root.set_label("[dim]No spec yet — run /init <name>[/dim]")
            self._update_detail(None)
            return
        name = spec.get("name", "spec")
        tree.root.set_label(f"[bold]Spec:[/bold] {name}")
        tree.root.expand()
        # Group capabilities by state
        caps = spec.get("capabilities") or []
        by_state: dict[str, list[dict]] = {k: [] for k, _, _ in _STATE_GROUPS}
        for cap in caps:
            state = cap.get("state", "backlog")
            if state in by_state:
                by_state[state].append(cap)
            else:
                by_state.setdefault(state, []).append(cap)
        for state_key, label, color in _STATE_GROUPS:
            items = by_state.get(state_key, [])
            count = len(items)
            node = tree.root.add(
                f"[{color}]{label}[/{color}] ({count})", expand=count > 0
            )
            for cap in items:
                cap_node = node.add_leaf(cap.get("title", "(untitled)"))
                cap_node.data = ("capability", cap.get("id"))
        # Non-goals
        non_goals = spec.get("non_goals") or []
        ng_node = tree.root.add(
            f"[#888888]Non-Goals[/#888888] ({len(non_goals)})", expand=True
        )
        for ng in non_goals:
            ng_leaf = ng_node.add_leaf(ng.get("text", "(empty)")[:50])
            ng_leaf.data = ("non_goal", ng.get("id"))
        self._update_detail(None)

    def _update_detail(self, target: tuple[str, str] | None) -> None:
        try:
            detail = self.query_one("#spec-detail", Static)
        except Exception:
            return
        if not self.spec:
            detail.update("[dim]No spec yet — run /init <name>[/dim]")
            return
        if target is None:
            spec = self.spec
            summary = spec.get("summary") or "(no summary)"
            n_caps = len(spec.get("capabilities") or [])
            n_ng = len(spec.get("non_goals") or [])
            detail.update(
                f"[bold]{spec.get('name', 'spec')}[/bold]\n"
                f"\n{summary}\n"
                f"\n[dim]capabilities:[/dim] {n_caps}"
                f"  [dim]non-goals:[/dim] {n_ng}"
                f"\n[dim]generated:[/dim] {spec.get('generated_at', '?')}"
            )
            return
        kind, item_id = target
        if kind == "capability":
            cap = next(
                (
                    c
                    for c in self.spec.get("capabilities") or []
                    if c.get("id") == item_id
                ),
                None,
            )
            if not cap:
                detail.update(f"[dim]capability not found: {item_id}[/dim]")
                return
            detail.update(self._render_capability(cap))
        elif kind == "non_goal":
            ng = next(
                (n for n in self.spec.get("non_goals") or [] if n.get("id") == item_id),
                None,
            )
            if not ng:
                detail.update(f"[dim]non-goal not found: {item_id}[/dim]")
                return
            detail.update(self._render_non_goal(ng))

    def _render_capability(self, cap: dict[str, Any]) -> str:
        lines: list[str] = []
        lines.append(f"[bold]{cap.get('title', '?')}[/bold]")
        lines.append(
            f"[dim]id:[/dim] {cap.get('id', '')}    [dim]state:[/dim] {cap.get('state', '?')}"
        )
        if cap.get("aliases"):
            lines.append(f"[dim]aliases:[/dim] {', '.join(cap['aliases'])}")
        if cap.get("summary"):
            lines.append("")
            lines.append(cap["summary"])
        story = cap.get("user_story")
        if story:
            lines.append("")
            lines.append("[bold]User story[/bold]")
            lines.append(f"  As {story.get('as', '?')},")
            lines.append(f"  I want {story.get('want', '?')},")
            lines.append(f"  so that {story.get('benefit', '?')}.")
        behaviors = cap.get("behaviors") or []
        if behaviors:
            lines.append("")
            lines.append("[bold]Behaviors[/bold]")
            for b in behaviors:
                lines.append(f"  • [{b.get('id', '?')}] {b.get('description', '')}")
                ac = b.get("acceptance_criteria") or []
                for c in ac:
                    lines.append(f"      - {c}")
                examples = b.get("examples") or []
                for ex in examples:
                    lines.append(f"      [dim]e.g.[/dim] {ex}")
        ac = cap.get("acceptance_criteria") or []
        if ac:
            lines.append("")
            lines.append("[bold]Acceptance criteria[/bold]")
            for c in ac:
                lines.append(f"  - {c}")
        excluded = cap.get("excluded") or []
        if excluded:
            lines.append("")
            lines.append("[bold]Excluded[/bold]")
            for e in excluded:
                lines.append(f"  - {e}")
        questions = cap.get("open_questions") or []
        if questions:
            lines.append("")
            lines.append("[bold]Open questions[/bold]")
            for q in questions:
                lines.append(f"  - {q}")
        tickets = cap.get("tickets") or []
        if tickets:
            lines.append("")
            lines.append("[dim]tickets:[/dim] " + ", ".join(tickets))
        return "\n".join(lines)

    def _render_non_goal(self, ng: dict[str, Any]) -> str:
        lines = [f"[bold]Non-goal:[/bold] {ng.get('text', '?')}"]
        lines.append(f"[dim]id:[/dim] {ng.get('id', '')}")
        if ng.get("aliases"):
            lines.append(f"[dim]aliases:[/dim] {', '.join(ng['aliases'])}")
        if ng.get("rationale"):
            lines.append("")
            lines.append(ng["rationale"])
        return "\n".join(lines)

    # --- selection -----------------------------------------------------------

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        node = event.node
        if node.data:
            self._update_detail(node.data)
        else:
            self._update_detail(None)

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted) -> None:
        node = event.node
        if node.data:
            self._update_detail(node.data)

    def action_select_next(self) -> None:
        try:
            self.query_one("#spec-tree", Tree).action_cursor_down()
        except Exception:
            return

    def action_select_prev(self) -> None:
        try:
            self.query_one("#spec-tree", Tree).action_cursor_up()
        except Exception:
            return
