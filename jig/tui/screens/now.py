from __future__ import annotations

import re

from rich.syntax import Syntax
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.message import Message
from textual.widgets import RichLog, Static, TextArea

from jig.tui.slash import ParsedSlash, SlashParseError, parse_slash


# Authoritative list of slash commands the operator can use. Drives both
# the popup list that appears above the input when the user starts typing /.
# NOTE: inline Suggester (ghost-text) is not available on TextArea; the popup
# is the only completion mechanism.
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


class JigTextArea(TextArea):
    """Multi-line input for Now. Enter submits; Shift+Enter inserts a newline.

    TextArea's default is the opposite (Enter inserts a newline, no submit).
    We override BINDINGS to swap them and emit a custom Submitted message
    on Enter so NowScreen's handler fires.
    """

    class Submitted(Message):
        """Posted when the operator presses Enter (without Shift)."""

        def __init__(self, value: str) -> None:
            super().__init__()
            self.value = value

    BINDINGS = [
        # Override Enter — TextArea's default inserts a newline; we submit.
        Binding("enter", "submit", "Submit", show=False, priority=True),
        Binding("shift+enter", "newline", "Newline", show=False),
    ]

    def action_submit(self) -> None:
        self.post_message(self.Submitted(self.text))

    def action_newline(self) -> None:
        self.insert("\n")


def _render_user_input(scrollback: RichLog, text: str) -> None:
    """Write the operator's submission to scrollback, rendering ```fenced```
    sections as Syntax blocks and the rest as plain text."""
    # Split on triple-backtick fences. Pattern: ```[lang]\n...code...\n```
    parts = re.split(r"```([a-zA-Z0-9_+-]*)\n([\s\S]*?)```", text)
    # parts looks like: [pre, lang1, code1, between, lang2, code2, post]
    if len(parts) == 1:
        # No code fences — write line-by-line with the cyan prefix
        for i, ln in enumerate(text.splitlines() or [""]):
            prefix = "[cyan]›[/cyan] " if i == 0 else "  "
            scrollback.write(f"{prefix}{ln}")
        return
    first = True
    for i in range(0, len(parts), 3):
        prose = parts[i]
        if prose:
            for ln in prose.splitlines():
                prefix = "[cyan]›[/cyan] " if first else "  "
                scrollback.write(f"{prefix}{ln}")
                first = False
        if i + 2 < len(parts):
            lang = parts[i + 1] or "text"
            code = parts[i + 2]
            scrollback.write(Syntax(code, lang, theme="ansi_dark", line_numbers=False))
            first = False


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
        height: auto;
        min-height: 3;
        max-height: 10;
        dock: bottom;
    }
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._active_prompt_id: str | None = None
        # Input history — up/down arrow recall.
        self._history: list[str] = []
        self._history_idx: int | None = None  # None = at the live edit; 0..len-1 = recall
        self._pending_value: str = ""

    def compose(self) -> ComposeResult:
        yield RichLog(id="scrollback", auto_scroll=True, markup=True)
        yield Static("", id="slash-popup", markup=True)
        yield JigTextArea(
            id="input",
            # TextArea doesn't support a placeholder; the welcome message
            # serves as the initial hint.
        )

    async def on_mount(self) -> None:
        self.query_one("#scrollback", RichLog).write(
            "[dim]welcome to jig — type /help or just type to ask the concierge[/dim]"
        )
        # Focus the input on launch so the operator can immediately type.
        self.query_one("#input", JigTextArea).focus()

    def on_show(self) -> None:
        """Re-focus the input whenever the Now pane becomes visible.

        Without this, switching to another pane and back leaves focus on
        the tab strip and the operator can't type until they click the input.
        """
        try:
            self.query_one("#input", JigTextArea).focus()
        except Exception:
            pass

    def _on_descendant_focus(self, event: events.DescendantFocus) -> None:
        """Stop DescendantFocus from reaching TabPane.

        If this event bubbles up, TabbedContent._on_tab_pane_focused will
        switch active back to now-pane whenever focus lands in the Input —
        even after the user has switched to another tab.
        """
        event.stop()

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        """Update the slash-command popup as the operator types."""
        if event.text_area.id != "input":
            return
        text = event.text_area.text
        # Slash popup only on single-line input that begins with /
        if "\n" in text:
            self._hide_slash_popup()
            return
        self._refresh_slash_popup(text)

    def on_key(self, event: events.Key) -> None:
        """Handle up/down arrows for input history recall.

        Only intercepts up/down when the cursor is at the boundary of the
        textarea — at the first line for up, last line for down. This lets
        multi-line editing work naturally via cursor movement.
        """
        try:
            ta = self.query_one("#input", JigTextArea)
        except Exception:
            return
        if not ta.has_focus:
            return
        if event.key not in ("up", "down"):
            return
        if not self._history:
            return

        row, _col = ta.cursor_location
        last_row = ta.document.line_count - 1

        if event.key == "up" and row != 0:
            # Not at first line — let TextArea move cursor up naturally
            return
        if event.key == "down" and row != last_row:
            # Not at last line — let TextArea move cursor down naturally
            return

        # Capture in-progress text once when starting to navigate history,
        # so down-arrow can return to it.
        if self._history_idx is None:
            self._pending_value = ta.text
            self._history_idx = len(self._history)  # one past the last

        if event.key == "up":
            if self._history_idx > 0:
                self._history_idx -= 1
                ta.text = self._history[self._history_idx]
                ta.move_cursor(ta.document.end, select=False)
            event.stop()
            event.prevent_default()
        elif event.key == "down":
            if self._history_idx < len(self._history) - 1:
                self._history_idx += 1
                ta.text = self._history[self._history_idx]
                ta.move_cursor(ta.document.end, select=False)
            else:
                # Past the end → restore the in-progress text
                self._history_idx = None
                ta.text = self._pending_value
                ta.move_cursor(ta.document.end, select=False)
            event.stop()
            event.prevent_default()

    def _record_history(self, text: str) -> None:
        """Append ``text`` to history (deduping consecutive identical entries)."""
        if not text:
            return
        if self._history and self._history[-1] == text:
            self._history_idx = None
            return
        self._history.append(text)
        # Cap history at 200 entries so it doesn't grow unbounded.
        if len(self._history) > 200:
            del self._history[: len(self._history) - 200]
        self._history_idx = None

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
        # TextArea doesn't have a placeholder attribute; write the hint to scrollback.
        scrollback.write(f"[dim]› {hint}[/dim]")

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

    def _clear_input(self) -> None:
        """Clear the textarea."""
        try:
            ta = self.query_one("#input", JigTextArea)
            ta.text = ""
        except Exception:
            pass

    async def on_jig_text_area_submitted(self, event: JigTextArea.Submitted) -> None:
        """Handle submission from JigTextArea (Enter key)."""
        text = event.value.strip()
        scrollback = self.query_one("#scrollback", RichLog)
        self._hide_slash_popup()
        self._record_history(text)

        # ANSWERING mode: route to prompt_reply
        if self._active_prompt_id is not None:
            prompt_id = self._active_prompt_id
            self._active_prompt_id = None
            if text:
                _render_user_input(scrollback, text)
            else:
                scrollback.write("[cyan]›[/cyan] [dim](empty)[/dim]")
            try:
                await self.app.client.send_command(
                    "prompt_reply", {"args": [prompt_id, text]}
                )
            except Exception as exc:
                scrollback.write(
                    f"[red]error:[/red] failed to send answer ({exc})"
                )
            self._clear_input()
            return

        # IDLE mode: existing slash dispatch
        if not text:
            self._clear_input()
            return
        _render_user_input(scrollback, text)
        if text.startswith("/"):
            # Bare `/` is a command-discovery shortcut — same render as /help
            if text == "/":
                await self._dispatch_slash(ParsedSlash(name="help", args=[]))
                self._clear_input()
                return
            try:
                parsed = parse_slash(text)
            except SlashParseError as exc:
                scrollback.write(f"[red]error:[/red] {exc}")
            else:
                await self._dispatch_slash(parsed)
        else:
            # Free-text → spawn the concierge with the input as the query.
            # The agent's text response streams back as agents/text events.
            await self.app.client.send_command("concierge", {"args": [text]})
        self._clear_input()

    # Keep a shim for tests / callers that still use the old Input.Submitted
    # fake-event pattern. The FakeEvent has a `.value` and `.input.clear()`;
    # this method matches the old signature so existing tests continue to work.
    async def on_input_submitted(self, event) -> None:  # type: ignore[override]
        """Legacy shim: accept FakeEvent objects from tests (Input.Submitted shape)."""
        text = event.value.strip()
        scrollback = self.query_one("#scrollback", RichLog)
        self._hide_slash_popup()
        self._record_history(text)

        if self._active_prompt_id is not None:
            prompt_id = self._active_prompt_id
            self._active_prompt_id = None
            if text:
                _render_user_input(scrollback, text)
            else:
                scrollback.write("[cyan]›[/cyan] [dim](empty)[/dim]")
            try:
                await self.app.client.send_command(
                    "prompt_reply", {"args": [prompt_id, text]}
                )
            except Exception as exc:
                scrollback.write(
                    f"[red]error:[/red] failed to send answer ({exc})"
                )
            try:
                event.input.clear()
            except Exception:
                pass
            return

        if not text:
            return
        _render_user_input(scrollback, text)
        if text.startswith("/"):
            if text == "/":
                await self._dispatch_slash(ParsedSlash(name="help", args=[]))
                try:
                    event.input.clear()
                except Exception:
                    pass
                return
            try:
                parsed = parse_slash(text)
            except SlashParseError as exc:
                scrollback.write(f"[red]error:[/red] {exc}")
            else:
                await self._dispatch_slash(parsed)
        else:
            await self.app.client.send_command("concierge", {"args": [text]})
        try:
            event.input.clear()
        except Exception:
            pass

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
                "[bold]Input editing[/bold]\n"
                "  Shift+Enter           insert a newline (multi-line input)\n"
                "  Enter                 submit\n"
                "  Up / Down arrows      recall previous / next submission\n"
                "                        (moves cursor within multi-line text)\n"
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
