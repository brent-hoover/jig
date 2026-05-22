from __future__ import annotations

import re

from rich.syntax import Syntax
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.css.query import NoMatches
from textual.message import Message
from textual.widgets import RichLog, Static, TextArea

from jig.tui.slash import ParsedSlash, SlashParseError, parse_slash
from jig.tui.widgets.multi_pane_stream import MultiPaneStream


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


# Stable color per role so the operator's eye can pick out which agent
# is talking at a glance. Falls back to bright_white for unknown roles.
_ROLE_COLORS: dict[str, str] = {
    "pm": "bright_cyan",
    "po": "cyan",
    "po-l0": "cyan",
    "po-l1": "cyan",
    "po-l2": "cyan",
    "po-l3": "cyan",
    "sa": "magenta",
    "spec": "bright_green",
    "spec-generator": "bright_green",
    "test": "bright_yellow",
    "dev": "bright_blue",
    "review": "yellow",
    "validate": "bright_yellow",
    "document": "white",
    "concierge": "bright_magenta",
    "quartermaster": "bright_white",
}


_ROLE_BG_TINTS: dict[str, str] = {
    "pm": "#0e2026",
    "po": "#0a1c20",
    "po-l0": "#0a1c20",
    "po-l1": "#0a1c20",
    "po-l2": "#0a1c20",
    "po-l3": "#0a1c20",
    "sa": "#22102a",
    "sa-mvp": "#22102a",
    "sa-v2": "#22102a",
    "spec": "#0e2410",
    "spec-generator": "#0e2410",
    "test": "#22220a",
    "dev": "#0a1024",
    "review": "#26200a",
    "validate": "#22220a",
    "document": "#1c1c1c",
    "concierge": "#22102a",
    "quartermaster": "#1c1c1c",
}

# Mid-brightness backgrounds for agent banners — dark enough for white text
# to be legible, bright enough to stand out from the terminal background.
_ROLE_BANNER_BG: dict[str, str] = {
    "pm": "#1a5a5a",
    "po": "#155055",
    "po-l0": "#155055",
    "po-l1": "#155055",
    "po-l2": "#155055",
    "po-l3": "#155055",
    "sa": "#56195a",
    "sa-mvp": "#56195a",
    "sa-v2": "#56195a",
    "spec": "#1e5c1e",
    "spec-generator": "#1e5c1e",
    "test": "#5a5a10",
    "dev": "#10205c",
    "review": "#5a4210",
    "validate": "#4a4a10",
    "document": "#383838",
    "concierge": "#4a1a5c",
    "quartermaster": "#383838",
}


def _role_bg_tint(role: str) -> str:
    return _ROLE_BG_TINTS.get(role, "#1c1c1c")


def _role_banner_bg(role: str) -> str:
    return _ROLE_BANNER_BG.get(role, "#303030")


_ROLE_FULL_NAMES: dict[str, str] = {
    "pm": "Project Manager",
    "po": "Product Owner",
    "po-l0": "Product Owner — L0 (Domain)",
    "po-l1": "Product Owner — L1 (Discovery)",
    "po-l2": "Product Owner — L2 (Suite)",
    "po-l3": "Product Owner — L3 (Capability)",
    "sa": "Solutions Architect",
    "sa-mvp": "Solutions Architect — MVP",
    "sa-v2": "Solutions Architect — v2",
    "spec": "Spec Author",
    "spec-generator": "Spec Generator",
    "test": "Test Author",
    "dev": "Developer",
    "review": "Reviewer",
    "validate": "Validator",
    "document": "Documentation",
    "concierge": "Concierge",
    "quartermaster": "Quartermaster",
}


def _role_color(role: str) -> str:
    return _ROLE_COLORS.get(role, "bright_white")


def _role_full_name(role: str) -> str:
    return _ROLE_FULL_NAMES.get(role, role.replace("-", " ").replace("_", " ").title())


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
        max-height: 22;
        dock: bottom;
        padding: 0 1 1 1;
        border-top: thick yellow;
        background: #252d40;
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
    #reviewer-panes {
        height: 50%;
        max-height: 30;
        dock: top;
        border-bottom: solid $accent;
        background: #1a2030;
        padding: 0 1;
        display: none;
    }
    #reviewer-panes.visible {
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
        Binding(
            "ctrl+r",
            "focus_reviewer_panes",
            "Focus reviewer panes",
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
        self._history_idx: int | None = (
            None  # None = at the live edit; 0..len-1 = recall
        )
        self._pending_value: str = ""
        self._scroll_paused: bool = False
        # Last agent role rendered to scrollback. Suppresses repeated
        # ``role:`` labels when the same agent emits consecutive turns
        # so the eye can scan multi-turn reasoning as one thought.
        self._last_role: str | None = None

    def compose(self) -> ComposeResult:
        # Multi-pane reviewer view docks above the scrollback when one or
        # more reviewers are running. Hidden by default; toggled visible
        # in ``_handle_reviewer_event`` when the first reviewer starts.
        yield MultiPaneStream(id="reviewer-panes", lines_per_pane=5)
        # ``min_width=0`` is critical. Textual's default of 78 forces
        # every rendered strip to be at least 78 cells wide, even when
        # the widget's visible content area is narrower (e.g. when the
        # right-docked Sidebar takes 36 cells off a 95-cell terminal,
        # leaving ~53 cells for scrollback). The overflow strips get
        # composited past the widget's right edge and bleed into the
        # Sidebar's column space — visible as truncated narrative text
        # ("...post it for approv") with leftover word fragments
        # ("by-s", "ing", "kets") floating in the gutter. Setting
        # min_width=0 lets ``shrink=True`` (the write-time default)
        # clamp the render width to the widget's actual content area,
        # so Markdown and other renderables wrap at the right place.
        yield RichLog(
            id="scrollback",
            auto_scroll=True,
            markup=True,
            wrap=True,
            min_width=0,
        )
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
            data.get("question_text") or data.get("question") or ""
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

        # Build the panel content.
        lines = [
            f"[bold black on bright_yellow] » ANSWER NEEDED [/bold black on bright_yellow] "
            f"[bold]{header_label}[/bold]"
        ]
        if body:
            lines.append("")
            lines.append(body)

        if options:
            opts = [self._normalize_option(o) for o in options]
            lines.append("")
            for o in opts:
                key = o["key"]
                label = o["label"]
                if o["default"]:
                    badge = f"[bold black on bright_yellow] {key} [/bold black on bright_yellow]"
                    lines.append(f"  {badge} {label} [dim](default)[/dim]")
                else:
                    badge = f"[bold black on bright_cyan] {key} [/bold black on bright_cyan]"
                    lines.append(f"  {badge} {label}")
            lines.append("")
            lines.append(
                "[dim]Type the key (e.g. "
                + ", ".join(f"[bold]{o['key']}[/bold]" for o in opts)
                + ") and press Enter.[/dim]"
            )
        elif prompt_type == "direct_template" and templates:
            lines.append("")
            lines.append(f"[bold]Pick a number 1–{len(templates)} below.[/bold]")
        else:
            lines.append("")
            lines.append("[bold]Type your answer below and press Enter.[/bold]")

        content = "\n".join(lines)
        panel.update(content)
        panel.set_class(True, "visible")
        self.set_class(True, "answering")

        # Compute the height needed for the actual content (incl. wrapping
        # of long question text) so the panel never clips its hint or
        # options. Falls back to a reasonable default if size isn't known
        # yet (first render before layout has run).
        screen_width = self.size.width or 100
        # Panel inner width = screen − sidebar − border − padding − slack.
        # We don't know the exact sidebar width here, so estimate
        # generously; a too-tall panel is fine, a too-short one clips.
        panel_inner = max(30, screen_width - 40)
        hard_lines = 0
        wrap_lines = 0
        # Strip Rich markup tags for length measurement only.
        plain_re = re.compile(r"\[/?[^\]]+\]")
        for line in content.splitlines() or [""]:
            hard_lines += 1
            visible_len = len(plain_re.sub("", line))
            if visible_len > panel_inner:
                wrap_lines += -(-visible_len // panel_inner) - 1  # ceil
        # +2 for the round border, +1 slack to avoid edge-case clipping.
        panel.styles.height = hard_lines + wrap_lines + 3

    def action_focus_reviewer_panes(self) -> None:
        """Move keyboard focus to the multi-pane reviewer widget so the
        operator can use its j/k/Enter/Esc/1-9 bindings.

        The widget's own bindings only fire when the widget itself (or
        a descendant) holds focus — without this entry point the
        bindings are advertised but unreachable because focus stays on
        the composer. ``Esc`` from within the widget blurs back to the
        composer (see ``MultiPaneStream.action_collapse``)."""
        try:
            panes = self.query_one("#reviewer-panes", MultiPaneStream)
        except NoMatches:
            return
        # No-op if the widget isn't visible (no reviewers active yet).
        if not panes.has_class("visible"):
            return
        try:
            panes.focus()
        except Exception:
            # ``Widget.focus()`` is best-effort during teardown.
            # ``NoMatches`` doesn't apply here (that's a query
            # exception); a broad catch matches the
            # ``screen.focus_next()`` pattern in
            # ``MultiPaneStream.action_collapse``.
            pass

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

    async def _send_command_safely(self, name: str, args: dict) -> bool:
        """Send a command to the daemon, surfacing errors to scrollback.

        Returns True on success, False on failure. The TUI would
        otherwise raise (e.g. ``RuntimeError: not connected``) up to
        Textual and crash the app — particularly bad when the daemon
        couldn't auto-start (port collision) and the operator just
        types ``/start``.
        """
        try:
            await self.app.client.send_command(name, args)
            return True
        except Exception as exc:  # noqa: BLE001
            try:
                scrollback = self.query_one("#scrollback", RichLog)
                state = getattr(self.app.daemon_state, "value", "unknown")
                scrollback.write(
                    f"[red]error:[/red] could not send /{name}: {exc} "
                    f"[dim](daemon: {state})[/dim]"
                )
            except Exception:
                pass
            return False

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
            # Reviewer agents (role starts with ``reviewer-``) get split
            # into a multi-pane view so concurrent reviewer text doesn't
            # interleave into the scrollback. Non-reviewer agents fall
            # through to the existing single-scrollback path.
            role = data.get("role", "")
            if isinstance(role, str) and role.startswith("reviewer-"):
                if self._handle_reviewer_event(kind, data):
                    return
            if kind == "render":
                content = data.get("content", "")
                if content:
                    # Agents emit Rich markup strings (e.g. [bold green]…[/]).
                    # Write directly so RichLog (markup=True) renders them.
                    # Text.from_ansi only handles ANSI escape codes and leaves
                    # Rich tags as literal text.
                    scrollback.write(content)
                return
            if kind == "start":
                from rich.text import Text

                role = data.get("role", "agent")
                color = _role_color(role)
                full_name = _role_full_name(role)
                ticket_id = data.get("ticket_id") or ""
                ticket_title = data.get("ticket_title") or ""
                phase = data.get("phase") or ""

                from rich.align import Align
                from rich.rule import Rule

                bg = _role_banner_bg(role)
                scrollback.write(Rule(style="dim white"))
                scrollback.write(
                    Align.center(
                        Text(full_name, style=f"bold #ffffff on {bg}"),
                        style=f"on {bg}",
                    )
                )
                if ticket_title:
                    scrollback.write(
                        Align.center(
                            Text(ticket_title, style=f"#f0f0f0 on {bg}"),
                            style=f"on {bg}",
                        )
                    )
                meta_bits: list[str] = []
                if phase:
                    meta_bits.append(f"phase: {phase}")
                if ticket_id:
                    meta_bits.append(f"ticket: {ticket_id[:8]}")
                if meta_bits:
                    scrollback.write(
                        Align.center(
                            Text("  ·  ".join(meta_bits), style=f"#cccccc on {bg}"),
                            style=f"on {bg}",
                        )
                    )
                scrollback.write(Rule(style="dim white"))
                # Force the next text turn to omit the role label — the banner
                # already identifies the speaker.
                self._last_role = role
                return
            if kind == "text":
                text = data.get("text", "")
                role = data.get("role", "agent")
                if text:
                    from rich.markdown import Markdown

                    # If this turn is from a different agent than the banner
                    # that was last shown, print a short colored label so the
                    # reader can tell who's talking without hunting for the
                    # last banner. When same-agent consecutive text arrives
                    # (multi-turn reasoning) suppress the label — the banner
                    # already identified the speaker.
                    if role != self._last_role:
                        color = _role_color(role)
                        scrollback.write(f"[bold {color}]{role}:[/bold {color}]")
                        self._last_role = role
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

    def _handle_reviewer_event(self, kind: str | None, data: dict) -> bool:
        """Route a reviewer-* agent event into the MultiPaneStream widget.

        Returns ``True`` when the event was consumed by the widget;
        the caller skips its normal scrollback render in that case.
        Returns ``False`` when the event is one we don't care about
        (e.g. ``render``) so the scrollback path can still take it.

        The widget becomes visible the moment the first reviewer
        starts and stays visible until the operator explicitly
        clears it (``escape`` from expanded mode collapses the pane
        but doesn't hide the widget). Phase advancement does NOT
        auto-hide — the operator may want to scroll back through
        any reviewer's history after the federation finishes.
        """
        try:
            panes = self.query_one("#reviewer-panes", MultiPaneStream)
        except NoMatches:
            return False
        role = data.get("role", "")
        ticket_id = data.get("ticket_id", "")
        # Per-(ticket, role) stream id keeps two parallel review-tests
        # phases on different tickets from sharing a pane.
        stream_id = f"{ticket_id}:{role}" if ticket_id else role
        if kind == "start":
            panes.add_stream(stream_id, label=role, status="running")
            panes.set_class(True, "visible")
            return True
        if kind == "text":
            text = data.get("text", "")
            if text:
                cleaned = _strip_rich_markup(text)
                panes.append_line(stream_id, cleaned)
            return True
        if kind == "thinking":
            # Consumed-but-ignored. ``active=False`` is NOT a terminal
            # signal for the agent — reviewers commonly cycle through
            # multiple thinking/text blocks, so flipping the status to
            # ``done`` on the first close would show ``✓ done`` while
            # more output is still arriving. The ``agents`` topic
            # doesn't emit a per-agent stop event today; until it
            # does, panes stay in ``running`` until the operator
            # dismisses the widget.
            return True
        if kind == "tool":
            # Surface tool calls as one-line entries so the operator
            # sees activity at a glance without leaving the pane view.
            tool = data.get("tool", "")
            detail = data.get("detail", "")
            if tool:
                if detail:
                    truncated = detail[:60] + ("…" if len(detail) > 60 else "")
                    line = f"tool: {tool}  {truncated}"
                else:
                    line = f"tool: {tool}"
                panes.append_line(stream_id, line)
            return True
        # Other event kinds (tool_result, render) — let the default
        # path handle them.
        return False

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
            pass  # shown in Recent sidebar; not needed here
        elif kind == "ticket_completed":
            scrollback.write(
                f"[green]✓[/green] completed [bold]{title}[/bold] ({ticket_id})"
            )
        elif kind == "ticket_failed":
            reason = data.get("reason", "")
            suffix = f" — {reason}" if reason else ""
            scrollback.write(
                f"[red]✗[/red] failed [bold]{title}[/bold] ({ticket_id}){suffix}"
            )
        elif kind == "ticket_merge_conflict":
            scrollback.write(
                f"[yellow]⚡[/yellow] merge conflict [bold]{title}[/bold] ({ticket_id})"
            )
        elif kind == "project_complete":
            from rich.align import Align
            from rich.rule import Rule
            from rich.text import Text

            resolved = data.get("tickets_resolved", 0)
            total = data.get("tickets_total", 0)
            scrollback.write(Rule(style="bold green"))
            scrollback.write(Align.center(Text("PROJECT COMPLETE", style="bold green")))
            if total:
                scrollback.write(
                    Align.center(
                        Text(f"{resolved}/{total} tickets resolved", style="dim green")
                    )
                )
            scrollback.write(Rule(style="bold green"))
        elif kind == "analysis_complete":
            outcome = data.get("outcome", "")
            out_dir = data.get("out_dir", "")
            suffix = f" — outcome: {outcome}" if outcome else ""
            scrollback.write(f"[dim green]📊 analysis complete{suffix}[/dim green]")
            if out_dir:
                scrollback.write(f"[dim]   report: {out_dir}[/dim]")

    @staticmethod
    def _normalize_option(opt) -> dict:
        """Coerce a plain string or partial dict into a full option dict."""
        if isinstance(opt, str):
            return {"key": opt[0].upper(), "label": opt, "default": False}
        return {
            "key": opt.get("key", ""),
            "label": opt.get("label", ""),
            "default": bool(opt.get("default")),
        }

    async def _render_prompt_request(self, data: dict) -> None:
        """Render a prompt request inline and switch to answering mode."""
        from rich.rule import Rule

        scrollback = self.query_one("#scrollback", RichLog)
        prompt_id = data.get("prompt_id")
        prompt_type = data.get("prompt_type", "")
        if not prompt_id:
            return  # malformed
        self._active_prompt_id = prompt_id
        self._active_prompt_type = prompt_type

        if prompt_type == "question_answer":
            asker = data.get("asker", "agent")
            idx = data.get("index", 1)
            total = data.get("total", 1)
            suffix = f" ({idx}/{total})" if total > 1 else ""
            scrollback.write(
                f"[dim cyan]› {asker} asked{suffix} — see prompt below[/dim cyan]"
            )
        else:
            rendered = data.get("rendered")
            if rendered:
                if prompt_type == "brief_approval":
                    from rich.markdown import Markdown

                    scrollback.auto_scroll = False
                    scrollback.write(Rule(title="Brief", style="yellow"))
                    scrollback.write(Markdown(rendered))
                    scrollback.write(Rule(style="yellow"))
                    self.call_after_refresh(scrollback.scroll_home)
                else:
                    from rich.markdown import Markdown

                    scrollback.write(Markdown(rendered))
            question = data.get("question")
            if question:
                scrollback.write(f"[bold yellow]?[/bold yellow] {question}")

        self._show_prompt_panel(data)
        hint = self._placeholder_hint(data)
        scrollback.write(f"[dim]› {hint} (see prompt below)[/dim]")

    @staticmethod
    def _placeholder_hint(data: dict) -> str:
        """Build a per-prompt-type hint shown in the input placeholder."""
        options = data.get("options") or []
        if options:
            opts = [NowScreen._normalize_option(o) for o in options]
            keys = "/".join(o["key"] for o in opts if o["key"])
            defaults = [o["key"] for o in opts if o["default"]]
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
                scrollback.write(f"[red]error:[/red] failed to send answer ({exc})")
            else:
                if prompt_type == "brief_approval":
                    scrollback.auto_scroll = True
                if prompt_type == "init_complete":
                    self.app._maybe_autostart_daemon()
                self.app._sidebar_safe(lambda s: s.update_prompt(None))
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
            await self._send_command_safely("concierge", {"args": [text]})
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
                scrollback.write(f"[red]error:[/red] failed to send answer ({exc})")
            else:
                if prompt_type == "brief_approval":
                    scrollback.auto_scroll = True
                if prompt_type == "init_complete":
                    self.app._maybe_autostart_daemon()
                self.app._sidebar_safe(lambda s: s.update_prompt(None))
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
            await self._send_command_safely("concierge", {"args": [text]})
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
            await self._send_command_safely(parsed.name, {"args": parsed.args})
