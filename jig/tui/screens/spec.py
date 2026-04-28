from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Static


class SpecScreen(Screen):
    BINDINGS = []

    def compose(self) -> ComposeResult:
        yield Static("Spec — coming in Phase 4")
