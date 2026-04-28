"""Top-level Textual app for jig."""
from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, TabbedContent, TabPane

from jig.tui.screens.events import EventsScreen
from jig.tui.screens.now import NowScreen
from jig.tui.screens.spec import SpecScreen
from jig.tui.screens.tickets import TicketsScreen


class JigApp(App):
    """The jig TUI. Single app, four tabbed screens."""

    CSS_PATH = "app.tcss"
    TITLE = "jig"

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("ctrl+c", "quit", "Quit", show=False),
        Binding("question_mark", "help", "Help"),
        Binding("1", "switch_screen('now')", "Now", show=False),
        Binding("2", "switch_screen('tickets')", "Tickets", show=False),
        Binding("3", "switch_screen('spec')", "Spec", show=False),
        Binding("4", "switch_screen('events')", "Events", show=False),
    ]

    def __init__(self, project_path: Path) -> None:
        super().__init__()
        self.project_path = project_path

    def compose(self) -> ComposeResult:
        with TabbedContent(initial="now-pane"):
            with TabPane("Now", id="now-pane"):
                yield NowScreen()
            with TabPane("Tickets", id="tickets-pane"):
                yield TicketsScreen()
            with TabPane("Spec", id="spec-pane"):
                yield SpecScreen()
            with TabPane("Events", id="events-pane"):
                yield EventsScreen()
        yield Footer()

    def action_switch_screen(self, screen_id: str) -> None:
        tabs = self.query_one(TabbedContent)
        tabs.active = f"{screen_id}-pane"

    def action_help(self) -> None:
        self.notify("Help overlay coming in Phase 2.4")
