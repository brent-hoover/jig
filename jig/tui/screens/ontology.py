"""Ontology screen — domain-vocabulary list + inline edit affordance.

Per ``docs/multi-level-spec/design.md`` §"Project ontology": the
operator wants to see captured terms at a glance and have an obvious
way to revise them. The screen surfaces every term (definition +
examples) plus a hint about the ``jig ontology …`` CLI commands so
operators know how to edit when they want to mutate a term.

Inline edit (typing into a TextArea) is wired through the daemon's
``/ontology`` slash command bridge — the screen itself stays
read-only to keep the data path simple. Final scope: visibility +
discoverability of the edit affordances.
"""
from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Container, Vertical
from textual.reactive import reactive
from textual.widgets import Static


class OntologyScreen(Container):
    """Project ontology display."""

    DEFAULT_CSS = """
    OntologyScreen {
        layout: vertical;
        height: 1fr;
        padding: 1 2;
    }
    """

    project_path: reactive[Path | None] = reactive(None, recompose=False)

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(id="ontology-header")
            yield Static(id="ontology-terms")
            yield Static(id="ontology-help")

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
            self.query_one("#ontology-header", Static).update(
                f"[bold]Ontology[/bold] [dim]({msg})[/dim]"
            )
            self.query_one("#ontology-terms", Static).update("")
            self.query_one("#ontology-help", Static).update("")
        except Exception:
            return

    def _render_state(self, project_path: Path) -> None:
        from jig.spec_loader import load_ontology

        try:
            ontology = load_ontology(project_path)
        except FileNotFoundError:
            self.query_one("#ontology-header", Static).update(
                "[bold]Ontology[/bold] [dim](no ontology.md on disk yet)[/dim]"
            )
            self.query_one("#ontology-terms", Static).update("")
            self.query_one("#ontology-help", Static).update(
                "[dim]Terms get captured automatically during the L1 "
                "discovery walk. The L1 PO calls ``ontology_stash_term`` "
                "and ``ontology_add_term`` MCP tools as terms surface.[/dim]"
            )
            return

        self.query_one("#ontology-header", Static).update(
            f"[bold]Ontology[/bold] ({len(ontology.terms)} terms)"
        )

        lines: list[str] = []
        for t in ontology.terms:
            lines.append(f"### [bold]{t.term}[/bold]")
            lines.append(t.definition)
            if t.examples:
                lines.append("[dim]Examples:[/dim]")
                for ex in t.examples:
                    lines.append(f"  - {ex}")
            lines.append("")
        if not lines:
            lines.append("[dim](no terms — captured terms will appear here)[/dim]")
        self.query_one("#ontology-terms", Static).update("\n".join(lines))

        self.query_one("#ontology-help", Static).update(
            "[dim]Edit:[/dim] [bold]jig ontology edit <term> "
            "--definition '...'[/bold]\n"
            "[dim]Remove:[/dim] [bold]jig ontology remove <term> "
            "[--replace-with X][/bold]\n"
            "[dim]Find references:[/dim] [bold]jig ontology "
            "find-references <term>[/bold]"
        )
