"""Spec view modals: raw YAML and rendered brief."""

from __future__ import annotations

from pathlib import Path

import yaml
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, ScrollableContainer
from textual.screen import ModalScreen
from textual.widgets import Static


class RawYamlModal(ModalScreen):
    """Show the structured spec as YAML, scrollable."""

    BINDINGS = [Binding("escape", "dismiss", "Close")]

    DEFAULT_CSS = """
    RawYamlModal {
        align: center middle;
    }
    RawYamlModal > Container {
        background: $panel;
        border: thick $accent;
        padding: 1 2;
        width: 90%;
        height: 90%;
    }
    RawYamlModal ScrollableContainer {
        height: 1fr;
    }
    """

    def __init__(self, spec: dict | None) -> None:
        super().__init__()
        self._spec = spec

    def compose(self) -> ComposeResult:
        with Container():
            yield Static(
                "[bold]Raw spec YAML[/bold] [dim](Esc to close)[/dim]", markup=True
            )
            with ScrollableContainer():
                if self._spec is None:
                    yield Static("[dim]No spec yet[/dim]", markup=True)
                else:
                    yield Static(yaml.safe_dump(self._spec, sort_keys=False))


class BriefModal(ModalScreen):
    """Show the rendered project brief markdown."""

    BINDINGS = [Binding("escape", "dismiss", "Close")]

    DEFAULT_CSS = """
    BriefModal {
        align: center middle;
    }
    BriefModal > Container {
        background: $panel;
        border: thick $accent;
        padding: 1 2;
        width: 90%;
        height: 90%;
    }
    BriefModal ScrollableContainer {
        height: 1fr;
    }
    """

    def __init__(self, project_path: Path) -> None:
        super().__init__()
        self._project_path = project_path

    def compose(self) -> ComposeResult:
        from rich.markdown import Markdown

        with Container():
            yield Static("[bold]Brief[/bold] [dim](Esc to close)[/dim]", markup=True)
            with ScrollableContainer():
                brief_path = self._project_path / "docs" / "brief.md"
                if not brief_path.is_file():
                    yield Static(
                        "[dim]No brief yet — run /init <name>[/dim]", markup=True
                    )
                else:
                    yield Static(Markdown(brief_path.read_text()))
