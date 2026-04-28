"""Top-level Textual app for jig."""
from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.reactive import reactive
from textual.widgets import TabbedContent, TabPane

from jig.daemon import daemon_paths
from jig.tui.daemon_client import ConnectionState, DaemonClient
from jig.tui.screens.events import EventsScreen
from jig.tui.screens.now import NowScreen
from jig.tui.screens.spec import SpecScreen
from jig.tui.screens.tickets import TicketsScreen
from jig.tui.widgets.footer import JigFooter


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

    daemon_state: reactive[ConnectionState] = reactive(ConnectionState.DISCONNECTED)

    def __init__(self, project_path: Path) -> None:
        super().__init__()
        self.project_path = project_path

        def _resolve_addr() -> str:
            addr_file = daemon_paths(self.project_path).socket_addr_file
            return (
                addr_file.read_text().strip()
                if addr_file.is_file()
                else "ws://127.0.0.1:9100"
            )

        self.client = DaemonClient(addr_provider=_resolve_addr)

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
        yield JigFooter()

    async def on_mount(self) -> None:
        self.run_worker(
            self.client.run_with_reconnect(
                on_message=self._handle_daemon_message,
                on_state_change=self._on_daemon_state,
                topics=["tickets", "spec", "agents", "events", "prompts"],
            ),
            exclusive=True,
            name="daemon-client",
        )

    async def _handle_daemon_message(self, msg: dict) -> None:
        msg_type = msg.get("type")
        if msg_type == "event":
            topic = msg.get("topic")
            if topic in ("agents", "prompts"):
                try:
                    now = self.query_one(NowScreen)
                except Exception:
                    return  # not mounted yet
                await now.handle_daemon_event(msg)
        elif msg_type == "result":
            try:
                now = self.query_one(NowScreen)
            except Exception:
                return
            await now.handle_command_result(msg)

    def _on_daemon_state(self, state: ConnectionState) -> None:
        self.daemon_state = state

    def watch_daemon_state(self, new: ConnectionState) -> None:
        try:
            footer = self.query_one(JigFooter)
        except Exception:
            return  # mount may not have run yet
        footer.update_daemon_state(new)

    def action_switch_screen(self, screen_id: str) -> None:
        tabs = self.query_one(TabbedContent)
        tabs.active = f"{screen_id}-pane"

    def action_help(self) -> None:
        from jig.tui.screens.help import HelpScreen

        self.push_screen(HelpScreen())
