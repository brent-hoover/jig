from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static

from jig.tui.daemon_client import ConnectionState


_COLORS = {
    ConnectionState.CONNECTED: "green",
    ConnectionState.CONNECTING: "yellow",
    ConnectionState.RECONNECTING: "yellow",
    ConnectionState.DISCONNECTED: "red",
}


class JigFooter(Widget):
    """Persistent footer: daemon state + key hints."""

    DEFAULT_CSS = """
    JigFooter {
        height: 1;
        dock: bottom;
        background: $panel;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._daemon_text = Static("daemon: ?")

    def compose(self) -> ComposeResult:
        yield self._daemon_text

    def update_daemon_state(self, state: ConnectionState) -> None:
        color = _COLORS.get(state, "white")
        self._daemon_text.update(f"[{color}]daemon: {state.value}[/{color}]")
