from __future__ import annotations

from textual import events
from textual.app import ComposeResult
from textual.containers import Container
from textual.suggester import SuggestFromList
from textual.widgets import Input, RichLog, Static

from jig.tui.slash import ParsedSlash, SlashParseError, parse_slash


# Authoritative list of slash commands the operator can use. Drives both
# the inline suggester (ghost-text completion) and the popup list that
# appears above the input when the user starts typing /.
_SLASH_COMMANDS: list[tuple[str, str]] = [
    ("/help", "show the command reference"),
    ("/status", "daemon + agent status"),
    ("/init", "initialize a project (no args = init cwd)"),
    ("/ticket new", "create a ticket (--title T --size s)"),
    ("/ticket update", "edit a ticket (<id> field=value)"),
    ("/concierge", "ask the concierge agent (or just type free text)"),
    ("/quit", "exit the TUI (also /q, /exit)"),
]
_SLASH_COMMAND_NAMES = [name for name, _ in _SLASH_COMMANDS]


class NowScreen(Container):
    """Active-interaction screen — scrolling transcript + input.

    Phase 3.3: answering mode (prompt_request) + agent render streaming.

    Note: DescendantFocus is stopped at this level to prevent Textual's
    TabPane.Focused / TabbedContent._on_tab_pane_focused chain from
    re-activating the now-pane whenever the Input gains focus.
    Without this, any focus() call inside this Container causes TabbedContent
    to switch back to now-pane.
    """

    DEFAULT_CSS = """
    NowScreen {
        layout: vertical;
        height: 1fr;
    }
    #scrollback {
        height: 1fr;
    }
    #slash-popup {
        height: auto;
        max-height: 10;
        dock: bottom;
        background: $panel;
        border-top: solid $accent;
        padding: 0 1;
        display: none;
    }
    #slash-popup.visible {
        display: block;
    }
    #input {
        height: 3;
        dock: bottom;
    }
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._active_prompt_id: str | None = None

    def compose(self) -> ComposeResult:
        yield RichLog(id="scrollback", auto_scroll=True, markup=True)
        yield Static("", id="slash-popup", markup=True)
        yield Input(
            id="input",
            placeholder="› type a slash command or message",
            suggester=SuggestFromList(_SLASH_COMMAND_NAMES, case_sensitive=False),
        )

    async def on_mount(self) -> None:
        self.query_one("#scrollback", RichLog).write(
            "[dim]welcome to jig — type /help or just type to ask the concierge[/dim]"
        )
        # Focus the input on launch so the operator can immediately type.
        # The bare digit bindings yield to a focused Input via App.check_action,
        # so global nav still works; Ctrl+1..4 always-fire for forced jumps.
        self.query_one("#input", Input).focus()

    def on_show(self) -> None:
        """Re-focus the input whenever the Now pane becomes visible.

        Without this, switching to another pane and back leaves focus on
        the tab strip and the operator can't type until they click the input.
        """
        try:
            self.query_one("#input", Input).focus()
        except Exception:
            pass

    def _on_descendant_focus(self, event: events.DescendantFocus) -> None:
        """Stop DescendantFocus from reaching TabPane.

        If this event bubbles up, TabbedContent._on_tab_pane_focused will
        switch active back to now-pane whenever focus lands in the Input —
        even after the user has switched to another tab.
        """
        event.stop()

    def on_input_changed(self, event: Input.Changed) -> None:
        """Update the slash-command popup as the operator types."""
        if event.input.id != "input":
            return
        self._refresh_slash_popup(event.value)

    def _refresh_slash_popup(self, value: str) -> None:
        try:
            popup = self.query_one("#slash-popup", Static)
        except Exception:
            return
        if not value.startswith("/"):
            popup.set_class(False, "visible")
            return
        # Filter commands by prefix-match against the typed value.
        prefix = value.lower()
        matches = [
            (name, desc)
            for name, desc in _SLASH_COMMANDS
            if name.lower().startswith(prefix) or prefix == "/"
        ]
        if not matches:
            popup.update(f"[dim]no matches for {value}[/dim]")
            popup.set_class(True, "visible")
            return
        # Render as a column of "  /name — description" lines.
        lines = ["[bold dim]commands[/bold dim]"]
        for name, desc in matches[:8]:
            lines.append(f"  [cyan]{name}[/cyan]  [dim]{desc}[/dim]")
        popup.update("\n".join(lines))
        popup.set_class(True, "visible")

    def _hide_slash_popup(self) -> None:
        try:
            self.query_one("#slash-popup", Static).set_class(False, "visible")
        except Exception:
            pass

    async def handle_daemon_event(self, msg: dict) -> None:
        """Fan-in handler called by JigApp when a relevant event arrives."""
        topic = msg.get("topic")
        data = msg.get("data") or {}
        scrollback = self.query_one("#scrollback", RichLog)
        if topic == "agents":
            kind = msg.get("kind")
            if kind == "render":
                content = data.get("content", "")
                if content:
                    scrollback.write(content)
                return
            if kind == "text":
                # Concierge / agent narration. Render with a role label.
                text = data.get("text", "")
                role = data.get("role", "agent")
                if text:
                    scrollback.write(f"[bold]{role}:[/bold] {text}")
                return
        if topic == "prompts" and msg.get("kind") == "request":
            await self._render_prompt_request(data)
            return

    async def _render_prompt_request(self, data: dict) -> None:
        """Render a prompt request inline and switch to answering mode."""
        scrollback = self.query_one("#scrollback", RichLog)
        prompt_id = data.get("prompt_id")
        prompt_type = data.get("prompt_type", "")
        if not prompt_id:
            return  # malformed
        self._active_prompt_id = prompt_id

        if prompt_type == "question_answer":
            from rich.panel import Panel
            from rich.text import Text

            q_text = data.get("question_text", "")
            asker = data.get("asker", "agent")
            idx = data.get("index", 1)
            total = data.get("total", 1)
            suffix = f" ({idx}/{total})" if total > 1 else ""
            scrollback.write(
                Panel(
                    Text(q_text, style="bold"),
                    title=f"[cyan]{asker} asks{suffix}[/cyan]",
                    title_align="left",
                    border_style="cyan",
                    padding=(0, 2),
                )
            )
        else:
            rendered = data.get("rendered")
            if rendered:
                scrollback.write(rendered)
            question = data.get("question")
            if question:
                scrollback.write(f"[bold]{question}[/bold]")

        hint = self._placeholder_hint(data)
        self.query_one("#input", Input).placeholder = f"› {hint}"

    @staticmethod
    def _placeholder_hint(data: dict) -> str:
        """Build a per-prompt-type hint shown in the input placeholder."""
        options = data.get("options") or []
        if options:
            keys = "/".join(o.get("key", "") for o in options if o.get("key"))
            defaults = [o.get("key") for o in options if o.get("default")]
            default = defaults[0] if defaults else None
            if default:
                return f"answer ({keys}, default {default})"
            return f"answer ({keys})"
        prompt_type = data.get("prompt_type", "")
        if prompt_type == "question_answer":
            return "type your answer"
        if prompt_type == "direct_template":
            templates = data.get("templates") or []
            if templates:
                return f"pick a number 1-{len(templates)}"
        return "type your answer"

    async def handle_command_result(self, msg: dict) -> None:
        """Surface daemon command results in scrollback."""
        scrollback = self.query_one("#scrollback", RichLog)
        if msg.get("ok"):
            data = msg.get("data")
            if data is None:
                scrollback.write("[green]✓ done[/green]")
            else:
                scrollback.write(f"[green]✓[/green] {data}")
        else:
            scrollback.write(f"[red]error:[/red] {msg.get('error', 'unknown')}")

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        line = event.value.strip()
        scrollback = self.query_one("#scrollback", RichLog)
        self._hide_slash_popup()

        # ANSWERING mode: route to prompt_reply
        if self._active_prompt_id is not None:
            prompt_id = self._active_prompt_id
            self._active_prompt_id = None
            # Restore default placeholder
            self.query_one("#input", Input).placeholder = (
                "› type a slash command or message"
            )
            if line:
                scrollback.write(f"[cyan]›[/cyan] {line}")
            else:
                scrollback.write("[cyan]›[/cyan] [dim](empty)[/dim]")
            try:
                await self.app.client.send_command(
                    "prompt_reply", {"args": [prompt_id, line]}
                )
            except Exception as exc:
                scrollback.write(
                    f"[red]error:[/red] failed to send answer ({exc})"
                )
            event.input.clear()
            return

        # IDLE mode: existing slash dispatch
        if not line:
            return
        scrollback.write(f"[cyan]›[/cyan] {line}")
        if line.startswith("/"):
            # Bare `/` is a command-discovery shortcut — same render as /help
            if line == "/":
                await self._dispatch_slash(ParsedSlash(name="help", args=[]))
                event.input.clear()
                return
            try:
                parsed = parse_slash(line)
            except SlashParseError as exc:
                scrollback.write(f"[red]error:[/red] {exc}")
            else:
                await self._dispatch_slash(parsed)
        else:
            # Free-text → spawn the concierge with the input as the query.
            # The agent's text response streams back as agents/text events.
            await self.app.client.send_command("concierge", {"args": [line]})
        event.input.clear()

    async def _dispatch_slash(self, parsed: ParsedSlash) -> None:
        scrollback = self.query_one("#scrollback", RichLog)
        if parsed.name == "help":
            scrollback.write(
                "[bold]Available commands[/bold] [dim](submit `/` alone for this list)[/dim]\n"
                "  /help                                 — show this message\n"
                "  /status                               — daemon + agent status\n"
                "  /init <name> [--force]                — initialize a project\n"
                "  /ticket new --title <t> --size <s>    — create a ticket\n"
                "  /ticket update <id> <field>=<value>   — edit a ticket\n"
                "  /concierge <query>                    — ask the concierge agent\n"
                "  /quit                                 — quit the TUI\n"
                "\n"
                "[bold]Per-pane hotkeys[/bold] (also see `?`)\n"
                "  Tickets:  n=new, e=edit, b=list/board, j/k=nav\n"
                "  Spec:     r=raw YAML, b=brief, j/k=nav\n"
                "  Events:   f=filter, F=follow, enter=detail\n"
                "\n"
                "[dim]Tip:[/dim] type free text (no leading /) to ask the concierge."
            )
        elif parsed.name == "status":
            scrollback.write(
                f"[bold]daemon:[/bold] {self.app.daemon_state.value}\n"
                "[dim]agent details coming in Phase 3.4[/dim]"
            )
        elif parsed.name in ("quit", "exit", "q"):
            self.app.exit()
        else:
            # Delegate everything else (including /init, future commands) to
            # the daemon dispatcher. Result/error envelope arrives via
            # handle_command_result.
            await self.app.client.send_command(parsed.name, {"args": parsed.args})
