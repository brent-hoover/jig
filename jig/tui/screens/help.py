from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Center
from textual.screen import ModalScreen
from textual.widgets import Static


_HELP = """\
[bold]Global keys[/bold]

  Ctrl+1 / 2 / 3 / 4   jump to Now / Tickets / Spec / Events  (always)
  1 / 2 / 3 / 4        same, but yields when Now's input has focus
  ? or F1              toggle this help (F1 works while typing)
  q / Ctrl+C           quit TUI (daemon keeps running)

[bold]Now screen[/bold]

  / + command       run a slash command
  Enter             submit input
  free text         routed to the concierge agent (Phase 3)

[bold]Slash commands[/bold]

  /help                              this overlay
  /status                            daemon + agent state
  /init <name>                       bootstrap a project (no args = cwd)
  /init --proceed                    advance the multi-level PO state machine
  /journey list | add <persona>      list / stage journeys
  /suite list | init <id> | refresh  L2/L3 dispatch
  /spec capabilities --suite <id>    list capabilities for a suite
  /concierge <query>                 ask the concierge agent
  /quit                              quit the TUI

[bold]Per-pane hotkeys[/bold]

  Tickets:    n = new ticket, e = edit selection, b = list / board
  Spec:       r = raw YAML, b = brief preview
  Discovery:  L1 PO state + captured personas / journeys / capabilities
  Suites:     L2 suites + L3 brief status
  Ontology:   project domain vocabulary (edit via `jig ontology …` CLI)
  Events:     f = cycle filter, F = toggle follow, enter = detail

[dim]Press Escape to close.[/dim]
"""


class HelpScreen(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss", "Close")]

    def compose(self) -> ComposeResult:
        with Center():
            yield Static(_HELP, id="help-text", markup=True)
