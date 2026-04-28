from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Static


class EventsScreen(Screen):
    BINDINGS = []

    def compose(self) -> ComposeResult:
        yield Static("Events — coming in Phase 4")
