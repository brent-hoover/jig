"""Modal showing a single bus event's full payload as pretty JSON."""
from __future__ import annotations

import json
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, ScrollableContainer
from textual.screen import ModalScreen
from textual.widgets import Static


class EventDetailModal(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss", "Close")]

    DEFAULT_CSS = """
    EventDetailModal {
        align: center middle;
    }
    EventDetailModal > Container {
        background: $panel;
        border: thick $accent;
        padding: 1 2;
        width: 90%;
        height: 80%;
    }
    EventDetailModal ScrollableContainer {
        height: 1fr;
    }
    """

    def __init__(self, event: dict[str, Any]) -> None:
        super().__init__()
        self._event = event

    def compose(self) -> ComposeResult:
        with Container():
            yield Static("[bold]Event detail[/bold] [dim](Esc to close)[/dim]", markup=True)
            with ScrollableContainer():
                yield Static(json.dumps(self._event, indent=2, default=str))
