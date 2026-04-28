from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Static


class TicketsScreen(Screen):
    BINDINGS = []

    def compose(self) -> ComposeResult:
        yield Static("Tickets — coming in Phase 4")
