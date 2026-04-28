from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Center
from textual.screen import ModalScreen
from textual.widgets import Static


_HELP = """\
[bold]Global keys[/bold]

  Tab / Shift+Tab   cycle screens
  1 / 2 / 3 / 4     jump to Now / Tickets / Spec / Events
  ?                 toggle this help
  q / Ctrl+C        quit TUI (daemon keeps running)

[bold]Now screen[/bold]

  / + command       run a slash command
  Enter             submit input
  free text         routed to the concierge agent (Phase 3)

[bold]Slash commands[/bold]

  /help     this overlay
  /status   daemon + agent state
  /quit     quit the TUI

[dim]Press Escape to close.[/dim]
"""


class HelpScreen(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss", "Close")]

    def compose(self) -> ComposeResult:
        with Center():
            yield Static(_HELP, id="help-text", markup=True)
