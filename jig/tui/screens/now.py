from __future__ import annotations

from textual import events
from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Input, RichLog

from jig.tui.slash import ParsedSlash, SlashParseError, parse_slash


class NowScreen(Screen):
    """Active-interaction screen — scrolling transcript + input.

    Phase 2: idle mode only. Slash commands /help, /status, /quit;
    free-text input prints a placeholder note (concierge in Phase 3).

    Note: AUTO_FOCUS is disabled and DescendantFocus is stopped at this
    level to prevent Textual's TabPane.Focused / TabbedContent._on_tab_pane_focused
    chain from re-activating the now-pane whenever the Input gains focus.
    Without this, any focus() call inside this Screen causes TabbedContent
    to switch back to now-pane.
    """

    AUTO_FOCUS = None

    DEFAULT_CSS = """
    NowScreen {
        layout: vertical;
    }
    #scrollback {
        height: 1fr;
    }
    #input {
        height: 3;
        dock: bottom;
    }
    """

    def compose(self) -> ComposeResult:
        yield RichLog(id="scrollback", auto_scroll=True, markup=True)
        yield Input(id="input", placeholder="› type a slash command or message")

    async def on_mount(self) -> None:
        self.query_one("#scrollback", RichLog).write(
            "[dim]welcome to jig — try /help[/dim]"
        )

    def on_show(self) -> None:
        """Focus the input when this pane becomes visible."""
        self.query_one("#input", Input).focus()

    def _on_descendant_focus(self, event: events.DescendantFocus) -> None:
        """Stop DescendantFocus from reaching TabPane.

        If this event bubbles up, TabbedContent._on_tab_pane_focused will
        switch active back to now-pane whenever focus lands in the Input —
        even after the user has switched to another tab.
        """
        event.stop()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        line = event.value.strip()
        if not line:
            return
        scrollback = self.query_one("#scrollback", RichLog)
        scrollback.write(f"[cyan]›[/cyan] {line}")
        if line.startswith("/"):
            try:
                parsed = parse_slash(line)
            except SlashParseError as exc:
                scrollback.write(f"[red]error:[/red] {exc}")
            else:
                await self._dispatch_slash(parsed)
        else:
            scrollback.write("[dim](free-text — concierge coming in Phase 3)[/dim]")
        event.input.clear()

    async def _dispatch_slash(self, parsed: ParsedSlash) -> None:
        scrollback = self.query_one("#scrollback", RichLog)
        if parsed.name == "help":
            scrollback.write(
                "[bold]Available commands:[/bold]\n"
                "  /help    — show this message\n"
                "  /status  — daemon + agent status\n"
                "  /quit    — quit the TUI"
            )
        elif parsed.name == "status":
            scrollback.write(
                f"[bold]daemon:[/bold] {self.app.daemon_state.value}\n"
                "[dim]agent details coming in Phase 3[/dim]"
            )
        elif parsed.name == "quit":
            self.app.exit()
        else:
            scrollback.write(
                f"[red]unknown command:[/red] /{parsed.name} "
                "[dim](try /help)[/dim]"
            )
