"""Reusable multi-pane stream view.

Renders N concurrent text streams as stacked panes. Each pane shows the
last K lines of its stream in compact mode; the operator can focus a
pane (``j`` / ``k``, number keys ``1``–``9``) and expand it
(``Enter``) to scroll back through the full buffer. ``Escape`` returns
to the compact view.

Intended use cases:

- Review-phase reviewer federation — five reviewer agents emit text
  concurrently; without per-reviewer panes their output interleaves
  into an unreadable mishmash. Each reviewer gets a stream.
- Any other "N concurrent producers, all worth following" surface
  the TUI grows later (parallel test runners, multi-suite sim runs,
  etc.). The widget is agnostic about what populates it.

The widget owns no domain knowledge. Callers (e.g. ``NowScreen``)
subscribe to events and translate them into ``add_stream`` /
``append_line`` / ``set_status`` calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from rich.console import Group
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widget import Widget
from textual.widgets import Static


StreamStatus = Literal["running", "done", "blocked", "waiting", "error"]


_STATUS_GLYPH: dict[str, str] = {
    "running": "⠋",
    "done": "✓",
    "blocked": "⊘",
    "waiting": "⏸",
    "error": "✗",
}

_STATUS_COLOR: dict[str, str] = {
    "running": "cyan",
    "done": "green",
    "blocked": "yellow",
    "waiting": "dim",
    "error": "red",
}


@dataclass
class _StreamState:
    """In-memory state for one stream pane.

    The widget keeps this stateful so callers can append lines
    one at a time without re-sending the whole buffer. ``lines``
    is unbounded — operators can scroll back through the full
    history when the pane is expanded. Trim externally if growth
    becomes a concern.
    """

    stream_id: str
    label: str
    status: StreamStatus = "running"
    finding_count: int | None = None
    lines: list[str] = field(default_factory=list)


class MultiPaneStream(Widget):
    """Stacked compact panes with focus-and-expand drill-down.

    Compact mode: every stream renders as a header (label + status
    icon + line counter) plus its last ``lines_per_pane`` lines.
    Expanded mode: just the focused stream, full buffer, scrollable.

    Public API:

    - ``add_stream(stream_id, label, status="running")`` — register
      a new pane. Idempotent; re-registering updates the label /
      status without dropping the buffer.
    - ``append_line(stream_id, line)`` — push one line into the
      stream. Auto-registers the stream if unknown so callers don't
      have to coordinate ordering.
    - ``set_status(stream_id, status, finding_count=None)`` — flip
      the status icon. ``finding_count`` is rendered after the icon
      when set (e.g. ``✓ done · 1F``).
    - ``set_label(stream_id, label)`` — rename a pane.
    - ``remove_stream(stream_id)`` — drop a pane entirely.
    - ``clear()`` — drop all streams (e.g. on phase transition).

    Default ``lines_per_pane`` is 5. The widget reads only its own
    state to render — Textual reactive plumbing is intentionally
    avoided so unit tests can drive the API without a running app.
    """

    DEFAULT_CSS = """
    MultiPaneStream {
        height: auto;
        layout: vertical;
    }
    MultiPaneStream > VerticalScroll {
        height: 100%;
    }
    MultiPaneStream .pane {
        height: auto;
        padding: 0 1;
        margin: 0 0 1 0;
        border-left: tall $surface;
    }
    MultiPaneStream .pane.focused {
        border-left: tall $accent;
    }
    """

    BINDINGS = [
        Binding("j", "focus_next", "Next pane", show=True),
        Binding("k", "focus_prev", "Prev pane", show=True),
        Binding("down", "focus_next", "Next pane", show=False),
        Binding("up", "focus_prev", "Prev pane", show=False),
        Binding("enter", "expand", "Expand", show=True),
        Binding("escape", "collapse", "Back", show=True),
        Binding("1", "focus_n(1)", show=False),
        Binding("2", "focus_n(2)", show=False),
        Binding("3", "focus_n(3)", show=False),
        Binding("4", "focus_n(4)", show=False),
        Binding("5", "focus_n(5)", show=False),
        Binding("6", "focus_n(6)", show=False),
        Binding("7", "focus_n(7)", show=False),
        Binding("8", "focus_n(8)", show=False),
        Binding("9", "focus_n(9)", show=False),
    ]

    def __init__(
        self,
        *,
        lines_per_pane: int = 5,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self._lines_per_pane = max(1, lines_per_pane)
        self._streams: dict[str, _StreamState] = {}
        self._order: list[str] = []
        self._focused: str | None = None
        self._expanded: bool = False
        self._dirty: bool = False

    # ── Public API ───────────────────────────────────────────────────

    def add_stream(
        self,
        stream_id: str,
        label: str,
        status: StreamStatus = "running",
    ) -> None:
        """Register or update a stream. Idempotent."""
        if stream_id in self._streams:
            self._streams[stream_id].label = label
            self._streams[stream_id].status = status
        else:
            self._streams[stream_id] = _StreamState(
                stream_id=stream_id,
                label=label,
                status=status,
            )
            self._order.append(stream_id)
            if self._focused is None:
                self._focused = stream_id
        self._mark_dirty()

    def append_line(self, stream_id: str, line: str) -> None:
        """Append one line to the stream. Auto-registers if unknown.

        Splits on embedded newlines so a multi-line text chunk
        renders as N lines rather than a single overflowing entry.
        """
        if stream_id not in self._streams:
            self.add_stream(stream_id, label=stream_id)
        state = self._streams[stream_id]
        for part in str(line).splitlines() or [str(line)]:
            state.lines.append(part)
        self._mark_dirty()

    def set_status(
        self,
        stream_id: str,
        status: StreamStatus,
        *,
        finding_count: int | None = None,
    ) -> None:
        """Update the status icon for a stream. Auto-registers if unknown."""
        if stream_id not in self._streams:
            self.add_stream(stream_id, label=stream_id, status=status)
        else:
            self._streams[stream_id].status = status
        self._streams[stream_id].finding_count = finding_count
        self._mark_dirty()

    def set_label(self, stream_id: str, label: str) -> None:
        if stream_id not in self._streams:
            return
        self._streams[stream_id].label = label
        self._mark_dirty()

    def remove_stream(self, stream_id: str) -> None:
        if stream_id not in self._streams:
            return
        self._streams.pop(stream_id)
        self._order.remove(stream_id)
        if self._focused == stream_id:
            self._focused = self._order[0] if self._order else None
            self._expanded = False
        self._mark_dirty()

    def clear(self) -> None:
        self._streams.clear()
        self._order.clear()
        self._focused = None
        self._expanded = False
        self._mark_dirty()

    @property
    def focused_stream_id(self) -> str | None:
        return self._focused

    @property
    def is_expanded(self) -> bool:
        return self._expanded

    def stream_ids(self) -> list[str]:
        """Stream ids in insertion order. Test/introspection helper."""
        return list(self._order)

    def stream_lines(self, stream_id: str) -> list[str]:
        """Full line buffer for a stream. Empty list for unknown ids."""
        if stream_id not in self._streams:
            return []
        return list(self._streams[stream_id].lines)

    # ── Textual lifecycle ────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Static(id="multi-pane-content")

    def on_mount(self) -> None:
        self._refresh()

    # ── Bindings ─────────────────────────────────────────────────────

    def action_focus_next(self) -> None:
        if not self._order:
            return
        if self._focused is None:
            self._focused = self._order[0]
        else:
            idx = self._order.index(self._focused)
            self._focused = self._order[(idx + 1) % len(self._order)]
        self._mark_dirty()

    def action_focus_prev(self) -> None:
        if not self._order:
            return
        if self._focused is None:
            self._focused = self._order[-1]
        else:
            idx = self._order.index(self._focused)
            self._focused = self._order[(idx - 1) % len(self._order)]
        self._mark_dirty()

    def action_focus_n(self, n: int) -> None:
        if not (1 <= n <= len(self._order)):
            return
        self._focused = self._order[n - 1]
        self._mark_dirty()

    def action_expand(self) -> None:
        if self._focused is None:
            return
        self._expanded = True
        self._mark_dirty()

    def action_collapse(self) -> None:
        if not self._expanded:
            return
        self._expanded = False
        self._mark_dirty()

    # ── Rendering ────────────────────────────────────────────────────

    def _mark_dirty(self) -> None:
        """Queue a refresh. Decoupled from the API entry points so
        rapid append_line calls coalesce into a single render."""
        self._dirty = True
        if self.is_mounted:
            self.call_later(self._refresh_if_dirty)

    def _refresh_if_dirty(self) -> None:
        if self._dirty:
            self._refresh()

    def _refresh(self) -> None:
        try:
            content = self.query_one("#multi-pane-content", Static)
        except Exception:
            # Widget not yet mounted; the on_mount hook will refresh.
            return
        if self._expanded and self._focused is not None:
            renderable = self._render_expanded()
        else:
            renderable = self._render_compact()
        content.update(renderable)
        self._dirty = False

    def _render_compact(self) -> Group:
        """Stacked panes, each showing the last ``lines_per_pane`` lines."""
        if not self._order:
            return Group(Text("(no streams)", style="dim"))
        blocks: list = []
        for stream_id in self._order:
            blocks.append(self._render_pane_compact(stream_id))
            blocks.append(Text(""))  # one-line gap between panes
        # Drop trailing gap.
        if blocks:
            blocks.pop()
        blocks.append(Text("", style="dim"))
        blocks.append(self._render_hints_compact())
        return Group(*blocks)

    def _render_pane_compact(self, stream_id: str) -> Group:
        state = self._streams[stream_id]
        is_focused = stream_id == self._focused
        marker = "[bold cyan]▶[/]" if is_focused else " "
        header = self._render_header(stream_id, marker=marker)
        total = len(state.lines)
        if total <= self._lines_per_pane:
            visible_lines = state.lines
        else:
            visible_lines = state.lines[-self._lines_per_pane :]
        body_blocks: list = []
        for line in visible_lines:
            body_blocks.append(Text("  " + line, no_wrap=False))
        if not visible_lines:
            dim_status = "starting…" if state.status == "running" else "(empty)"
            body_blocks.append(Text(f"  {dim_status}", style="dim italic"))
        counter = Text(
            f"  ({len(visible_lines)} / {total} lines)",
            style="dim",
        )
        body_blocks.append(counter)
        return Group(header, *body_blocks)

    def _render_expanded(self) -> Group:
        stream_id = self._focused
        assert stream_id is not None
        state = self._streams[stream_id]
        header = self._render_header(stream_id, marker="[bold cyan]▼[/]", expanded=True)
        body_blocks: list = []
        if not state.lines:
            body_blocks.append(Text("  (no output yet)", style="dim italic"))
        else:
            for line in state.lines:
                body_blocks.append(Text("  " + line, no_wrap=False))
        body_blocks.append(Text(""))
        body_blocks.append(self._render_hints_expanded())
        return Group(header, *body_blocks)

    def _render_header(
        self,
        stream_id: str,
        *,
        marker: str,
        expanded: bool = False,
    ) -> Text:
        state = self._streams[stream_id]
        idx = self._order.index(stream_id) + 1
        glyph = _STATUS_GLYPH.get(state.status, "·")
        color = _STATUS_COLOR.get(state.status, "white")
        suffix = state.status
        if state.finding_count is not None:
            suffix = f"{state.status}, {state.finding_count}F"
        head = Text.from_markup(
            f"{marker} [bold]{state.label}[/bold]  "
            f"[{color}]{glyph}[/{color}] [{color}]{suffix}[/{color}]"
            f"  [dim](pane {idx})[/dim]"
        )
        return head

    def _render_hints_compact(self) -> Text:
        return Text.from_markup(
            "[dim]j/k  navigate    Enter  expand & scroll    1-9  jump[/dim]"
        )

    def _render_hints_expanded(self) -> Text:
        return Text.from_markup(
            "[dim]Esc  back to all panes    j/k  next/prev pane[/dim]"
        )
