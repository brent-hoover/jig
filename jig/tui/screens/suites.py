"""Suites screen — list of L2 suites + their L3 brief status.

Per ``docs/v2.0/multi-level-spec/design.md`` §"L2 — Suite Organizer" + §"L3
— Suite Brief": the operator wants to see the full suite list at a
glance, with each suite's brief status (pending / brief_ready /
resolved) and capability list.

Read-only screen — authoring goes through the L2 / L3 PO MCP tools.
"""
from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Container, Vertical
from textual.reactive import reactive
from textual.widgets import Static


class SuitesScreen(Container):
    """L2 suites + L3 brief status."""

    DEFAULT_CSS = """
    SuitesScreen {
        layout: vertical;
        height: 1fr;
        padding: 1 2;
    }
    """

    project_path: reactive[Path | None] = reactive(None, recompose=False)

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(id="suites-header")
            yield Static(id="suites-list")

    def on_mount(self) -> None:
        self.refresh_now()

    def set_project_path(self, project_path: Path | None) -> None:
        self.project_path = project_path
        self.refresh_now()

    def refresh_now(self) -> None:
        if self.project_path is None:
            self._render_empty("no project_path set")
            return
        try:
            self._render_state(self.project_path)
        except Exception as exc:  # noqa: BLE001
            self._render_empty(f"error: {exc}")

    def _render_empty(self, msg: str) -> None:
        try:
            self.query_one("#suites-header", Static).update(
                f"[bold]Suites[/bold] [dim]({msg})[/dim]"
            )
            self.query_one("#suites-list", Static).update("")
        except Exception:
            return

    def _render_state(self, project_path: Path) -> None:
        from jig.spec_loader import load_suites_index

        try:
            index = load_suites_index(project_path)
        except FileNotFoundError:
            self.query_one("#suites-header", Static).update(
                "[bold]Suites[/bold] [dim](no suites.yaml on disk; "
                "run /init --proceed to author L2)[/dim]"
            )
            self.query_one("#suites-list", Static).update("")
            return

        self.query_one("#suites-header", Static).update(
            f"[bold]Suites[/bold] ({len(index.suites)} total)"
        )

        lines: list[str] = []
        for s in index.suites:
            brief = (
                project_path / ".jig" / "spec" / "suites" / s.id / "brief.md"
            )
            status = "[yellow]pending[/yellow]"
            if brief.is_file():
                status = "[green]brief_ready[/green]"
            lines.append(
                f"- [bold]{s.title}[/bold] {{#{s.id}}}  {status}\n"
                f"  [dim]{s.summary}[/dim]\n"
                f"  [dim]capabilities: {', '.join(s.capabilities) or '(none)'}[/dim]"
            )
        if not lines:
            lines.append("[dim](no suites)[/dim]")
        self.query_one("#suites-list", Static).update("\n".join(lines))
