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
    ("/init --proceed", "advance the multi-level PO state machine"),
    ("/ticket new", "create a ticket (--title T --size s)"),
    ("/ticket update", "edit a ticket (<id> field=value)"),
    ("/journey list", "list captured journeys + playbacks"),
    ("/journey add", "stage a new journey under <persona>"),
    ("/suite list", "list suites with brief status"),
    ("/suite init", "spawn L3 PO scoped to <id>"),
    ("/suite refresh", "re-author the L3 brief for <id>"),
    ("/spec capabilities", "list capabilities (--suite <id>)"),
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

    def _on_paste(self, event) -> None:
        """Insert pasted text directly, preserving embedded newlines.

        Without this, terminals that don't support bracketed paste (or
        when Textual fails to detect it) translate each ``\\n`` in the
        paste buffer into a synthetic ``enter`` key event, which our
        priority Enter→submit binding catches and fires submit on. The
        result: a multi-line paste gets split and submitted in pieces.
        Handling Paste explicitly here inserts the full payload as text
        and stops the event before any Enter binding can fire on the
        embedded newlines.
        """
        text = getattr(event, "text", None)
        if text is None:
            return
        try:
            self.insert(text)
        finally:
            try:
                event.stop()
            except Exception:
                pass


_RICH_TAG_RE = re.compile(r"\[/?[a-zA-Z][a-zA-Z0-9_ ]*\]")


def _strip_rich_markup(text: str) -> str:
    """Remove Rich markup tags so Markdown rendering doesn't show them literally.

    Agents sometimes emit Rich-style ``[bold]X[/bold]`` / ``[dim]Y[/dim]``
    tags inline with Markdown prose. ``rich.markdown.Markdown`` doesn't
    interpret those tags, so they leak into the rendered output as raw
    text. Stripping them here keeps the underlying content (e.g.
    ``X``, ``Y``) and lets Markdown handle ``**bold**`` / ``_italic_``
    properly.
    """
    return _RICH_TAG_RE.sub("", text)


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
    #thinking {
        height: 1;
        dock: bottom;
        padding: 0 1;
        color: $accent;
        background: #1a2030;
        display: none;
    }
    #thinking.visible {
        display: block;
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
    #prompt-panel {
        height: auto;
        max-height: 14;
        dock: bottom;
        padding: 0 1;
        border: round yellow;
        background: $panel;
        color: $text;
        display: none;
    }
    #prompt-panel.visible {
        display: block;
    }
    #input {
        height: auto;
        min-height: 3;
        max-height: 10;
        dock: bottom;
    }
    NowScreen.answering #input {
        border: round yellow;
    }
    #scroll-pause {
        height: 1;
        dock: bottom;
        padding: 0 1;
        color: black;
        background: ansi_bright_yellow;
        display: none;
    }
    #scroll-pause.visible {
        display: block;
    }
    """

    BINDINGS = [
        Binding(
            "ctrl+p",
            "toggle_pause_scroll",
            "Pause/resume scrollback auto-scroll",
            show=False,
            priority=True,
        ),
    ]

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._active_prompt_id: str | None = None
        self._active_prompt_type: str | None = None
        # Input history — up/down arrow recall.
        self._history: list[str] = []
        self._history_idx: int | None = None  # None = at the live edit; 0..len-1 = recall
        self._pending_value: str = ""
        self._scroll_paused: bool = False

    def compose(self) -> ComposeResult:
        yield RichLog(id="scrollback", auto_scroll=True, markup=True, wrap=True)
        # Thinking indicator (live, in-place updates — replaces the broken
        # \r-overwriting rich Status spinner).
        yield Static("", id="thinking", markup=True)
        yield Static("", id="slash-popup", markup=True)
        yield Static("", id="prompt-panel", markup=True)
        yield Static("", id="scroll-pause", markup=True)
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

        Also handles 'y' on the focused scrollback to copy content to clipboard.
        """
        # Copy scrollback to clipboard when 'y' is pressed on the focused RichLog.
        try:
            scrollback = self.query_one("#scrollback", RichLog)
        except Exception:
            scrollback = None
        if scrollback is not None and scrollback.has_focus and event.key == "y":
            self._copy_scrollback_to_clipboard()
            event.stop()
            return

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

    def _copy_scrollback_to_clipboard(self) -> None:
        """Copy all scrollback text to the macOS clipboard via pbcopy."""
        import subprocess

        try:
            scrollback = self.query_one("#scrollback", RichLog)
        except Exception:
            return
        text = "\n".join(strip.text for strip in scrollback.lines)
        try:
            subprocess.run(["pbcopy"], input=text.encode(), check=False, timeout=2)
            self.app.notify("Scrollback copied", timeout=2)
        except Exception as exc:
            self.app.notify(f"Copy failed: {exc}", severity="warning", timeout=3)

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

    def _show_prompt_panel(self, data: dict) -> None:
        """Render the pinned prompt panel above the input.

        Always-visible while a prompt is active so the operator can't miss
        it. The panel shows the *actual question* (truncated if very long)
        plus an options/answer hint — operator should never have to scroll
        the scrollback to remember what they're answering.
        """
        try:
            panel = self.query_one("#prompt-panel", Static)
        except Exception:
            return

        prompt_type = data.get("prompt_type") or "input"
        asker = (data.get("asker") or "agent").strip()
        options = data.get("options") or []
        templates = data.get("templates") or []
        question_text = (
            data.get("question_text")
            or data.get("question")
            or ""
        ).strip()

        type_map = {
            "question_answer": f"Question from {asker}",
            "brief_approval": "Approve brief",
            "init_complete": "Init complete",
            "needs_info": f"{asker} needs input",
            "direct_template": "Pick a template",
            "sa_confirm": "Confirm template",
        }
        header_label = type_map.get(prompt_type, prompt_type.replace("_", " ").title())

        # Truncate very long questions so the panel doesn't blow past
        # max-height. The full text remains in scrollback above.
        body = question_text
        if len(body) > 600:
            body = body[:597].rstrip() + "…"

        if options:
            opt_parts = []
            for opt in options:
                key = opt.get("key", "")
                label = opt.get("label", "")
                if opt.get("default"):
                    opt_parts.append(
                        f"[bold black on bright_yellow] {key} [/bold black on bright_yellow] {label} (default)"
                    )
                else:
                    opt_parts.append(
                        f"[bold black on bright_cyan] {key} [/bold black on bright_cyan] {label}"
                    )
            hint = "   ".join(opt_parts)
        elif prompt_type == "direct_template" and templates:
            hint = "[bold]Pick a number 1–%d below.[/bold]" % len(templates)
        else:
            hint = "[bold]Type your answer below and press Enter.[/bold]"

        # Header line + (optional) question body + hint. Use Rich markup
        # throughout so all three pieces render with consistent styling.
        lines = [
            f"[bold black on bright_yellow] » ANSWER NEEDED [/bold black on bright_yellow] "
            f"[bold]{header_label}[/bold]"
        ]
        if body:
            lines.append("")
            lines.append(body)
        lines.append("")
        lines.append(hint)

        panel.update("\n".join(lines))
        panel.set_class(True, "visible")
        self.set_class(True, "answering")

    def action_toggle_pause_scroll(self) -> None:
        """Pause / resume scrollback auto-scroll.

        When paused: auto_scroll is off, focus moves to the scrollback so
        PgUp/PgDn/arrow keys navigate it natively, and a "PAUSED" status
        bar appears just above the input.
        When resumed: auto_scroll re-enables, focus returns to the input,
        and the scrollback jumps to the latest content.
        """
        try:
            scrollback = self.query_one("#scrollback", RichLog)
            indicator = self.query_one("#scroll-pause", Static)
        except Exception:
            return
        self._scroll_paused = not self._scroll_paused
        scrollback.auto_scroll = not self._scroll_paused
        if self._scroll_paused:
            indicator.update(
                "[bold]⏸ SCROLL PAUSED[/bold]  PgUp/PgDn/↑↓ to navigate · "
                "[bold]y[/bold] copy all · [bold]Ctrl+P[/bold] resume"
            )
            indicator.set_class(True, "visible")
            try:
                scrollback.focus()
            except Exception:
                pass
        else:
            indicator.set_class(False, "visible")
            indicator.update("")
            try:
                scrollback.scroll_end(animate=False)
            except Exception:
                pass
            try:
                self.query_one("#input", JigTextArea).focus()
            except Exception:
                pass

    def _hide_prompt_panel(self) -> None:
        try:
            panel = self.query_one("#prompt-panel", Static)
            panel.set_class(False, "visible")
            panel.update("")
        except Exception:
            pass
        self.set_class(False, "answering")

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
                    from rich.text import Text
                    scrollback.write(Text.from_ansi(content))
                return
            if kind == "start":
                from rich.rule import Rule
                role = data.get("role", "agent")
                scrollback.write(Rule(f"[bold cyan]{role}[/bold cyan]", style="cyan"))
                return
            if kind == "text":
                # Concierge / agent narration. Render with a role label.
                text = data.get("text", "")
                role = data.get("role", "agent")
                if text:
                    from rich.markdown import Markdown
                    scrollback.write(f"[bold]{role}:[/bold]")
                    # Agents emit a mix of Markdown (**bold**) and Rich
                    # markup ([bold]X[/bold]). Markdown rendering treats
                    # the Rich tags as literal text — strip them so the
                    # underlying content renders cleanly.
                    cleaned = _strip_rich_markup(text)
                    scrollback.write(Markdown(cleaned))
                return
            if kind == "thinking":
                self._update_thinking_indicator(data)
                return
        if topic == "prompts" and msg.get("kind") == "request":
            await self._render_prompt_request(data)
            return
        if topic == "events":
            self._render_lifecycle_event(msg.get("kind", ""), data, scrollback)
            return

    def _update_thinking_indicator(self, data: dict) -> None:
        """Show / hide / refresh the live thinking indicator above Composer."""
        try:
            indicator = self.query_one("#thinking", Static)
        except Exception:
            return
        active = data.get("active", False)
        if not active:
            indicator.set_class(False, "visible")
            indicator.update("")
            return
        role = data.get("role", "agent")
        elapsed = int(data.get("elapsed", 0))
        # Rotating Braille spinner driven by elapsed seconds — no \r needed
        # because Static.update replaces the cell content in place.
        spinners = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        spin = spinners[elapsed % len(spinners)]
        indicator.update(
            f"[dim]{spin}[/dim] [bold]{role}[/bold] "
            f"[dim]is thinking… ({elapsed}s)[/dim]"
        )
        indicator.set_class(True, "visible")

    def _render_lifecycle_event(self, kind: str, data: dict, scrollback) -> None:
        ticket_id = data.get("ticket_id", "?")
        title = data.get("title", ticket_id)
        if kind == "ticket_dispatched":
            scrollback.write(f"[cyan]▶[/cyan] dispatching [bold]{title}[/bold] ({ticket_id})")
        elif kind == "ticket_completed":
            scrollback.write(f"[green]✓[/green] completed [bold]{title}[/bold] ({ticket_id})")
        elif kind == "ticket_failed":
            reason = data.get("reason", "")
            suffix = f" — {reason}" if reason else ""
            scrollback.write(f"[red]✗[/red] failed [bold]{title}[/bold] ({ticket_id}){suffix}")
        elif kind == "ticket_merge_conflict":
            scrollback.write(f"[yellow]⚡[/yellow] merge conflict [bold]{title}[/bold] ({ticket_id})")

    async def _render_prompt_request(self, data: dict) -> None:
        """Render a prompt request inline and switch to answering mode."""
        scrollback = self.query_one("#scrollback", RichLog)
        prompt_id = data.get("prompt_id")
        prompt_type = data.get("prompt_type", "")
        if not prompt_id:
            return  # malformed
        self._active_prompt_id = prompt_id
        self._active_prompt_type = prompt_type

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
                if prompt_type == "brief_approval":
                    # Brief can be long — disable auto-scroll and restore
                    # the view to the top of the preview so the operator
                    # reads from the start rather than landing at the end.
                    scrollback.auto_scroll = False
                    scrollback.write(rendered)
                    self.call_after_refresh(scrollback.scroll_home)
                else:
                    scrollback.write(rendered)
            question = data.get("question")
            if question:
                from rich.markdown import Markdown
                scrollback.write(Markdown(question))

        # Show the dedicated, always-visible prompt panel above the input.
        # The full question stays in scrollback above; the panel is a small
        # consistent indicator so the operator can't miss that an answer is
        # needed even if the scrollback has scrolled past.
        self._show_prompt_panel(data)
        hint = self._placeholder_hint(data)
        scrollback.write(f"[dim]› {hint} (see prompt below)[/dim]")

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
            # Only surface human-readable string results; dict payloads are
            # internal plumbing (e.g. {prompt_id: ...}) and should not appear.
            if isinstance(data, str) and data:
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
            # Guard: slash commands are never valid prompt answers.
            # The user likely forgot they were in answer mode.
            if text.startswith("/"):
                scrollback.write(
                    "[yellow]answer mode:[/yellow] type your answer (e.g. [bold]y[/bold] or [bold]n[/bold]) and press Enter. "
                    "Slash commands are disabled while a prompt is active."
                )
                self._clear_input()
                return
            prompt_id = self._active_prompt_id
            prompt_type = self._active_prompt_type
            self._active_prompt_id = None
            self._active_prompt_type = None
            self._hide_prompt_panel()
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
            if prompt_type == "brief_approval":
                scrollback.auto_scroll = True
            if prompt_type == "init_complete":
                self.app._maybe_autostart_daemon()
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
            prompt_type = self._active_prompt_type
            self._active_prompt_id = None
            self._active_prompt_type = None
            self._hide_prompt_panel()
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
            if prompt_type == "brief_approval":
                scrollback.auto_scroll = True
            if prompt_type == "init_complete":
                self.app._maybe_autostart_daemon()
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
                "  /init --proceed                       — advance the PO state machine\n"
                "  /ticket new --title <t> --size <s>    — create a ticket\n"
                "  /ticket update <id> <field>=<value>   — edit a ticket\n"
                "  /journey list | add <persona>         — list / stage journeys\n"
                "  /suite list | init <id> | refresh <id>— L2/L3 dispatch\n"
                "  /spec capabilities --suite <id>       — capabilities for a suite\n"
                "  /concierge <query>                    — ask the concierge agent\n"
                "  /quit                                 — quit the TUI\n"
                "\n"
                "[bold]Per-pane hotkeys[/bold] (also see `?`)\n"
                "  Tickets:  n=new, e=edit, b=list/board, j/k=nav\n"
                "  Spec:     r=raw YAML, b=brief, j/k=nav\n"
                "  Events:   f=filter, F=follow, enter=detail\n"
                "\n"
                "[bold]Input editing[/bold]\n"
                "  Enter                 submit\n"
                "  Shift+Enter           insert a newline (multi-line input)\n"
                "  Ctrl+A / Ctrl+E       jump to start / end of line\n"
                "  Ctrl+W                delete word left\n"
                "  Ctrl+U / Ctrl+K       delete to start / end of line\n"
                "  Up / Down arrows      recall previous / next submission\n"
                "                        (or move within multi-line text)\n"
                "  Ctrl+I                paste clipboard image (saves to .jig/uploads/)\n"
                "  Ctrl+P                pause/resume scrollback auto-scroll\n"
                "  Ctrl+S                toggle right-side Sidebar\n"
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
