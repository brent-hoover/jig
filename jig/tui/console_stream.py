"""Streaming rich.Console that emits each chunk as a JigEvent.

Used by the daemon-side init flow to ship rendered output (rules,
panels, spinner status, markdown previews) to TUI clients via the
EventEmitter / WebSocket protocol.

Each Console.print() invocation flushes to the underlying _StreamFile,
which buffers and emits per write boundary. The TUI receives
``{type: event, topic: agents, kind: render, data: {content: "..."}}``
envelopes and writes the (ANSI-rendered) content into its RichLog.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jig.events import EventEmitter


class _StreamFile:
    """File-like sink that batches writes per flush and emits one JigEvent
    per ``Console.print`` call through the EventEmitter.

    Rich calls ``write()`` multiple times per ``print()`` call (once per
    ANSI segment) and then calls ``flush()``. Emitting on each write
    fragments ANSI escape sequences across separate events, which breaks
    ``Text.from_ansi`` on the TUI side and causes visual garbage on
    resize. Buffering until flush emits a single, complete ANSI string
    per print call.
    """

    def __init__(self, emitter: "EventEmitter") -> None:
        self._emitter = emitter
        self._buf: list[str] = []

    def write(self, text: str) -> int:
        if not text:
            return 0
        self._buf.append(text)
        return len(text)

    def flush(self) -> None:
        if not self._buf:
            return
        content = "".join(self._buf)
        self._buf.clear()
        from jig.events import JigEvent

        # The emitter's emit is async; schedule it without blocking.
        # If no loop is running (called from sync code outside the event
        # loop), drop the write — the daemon always owns the loop.
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(
            self._emitter.emit(JigEvent(type="agent_render", data={"content": content}))
        )

    def isatty(self) -> bool:
        # Force rich to render colors / ANSI for us.
        return True


def make_streaming_console(emitter: "EventEmitter"):
    """Build a rich.Console that ships its output through ``emitter``.

    ANSI escape sequences are emitted as part of the content; the
    TUI's ``RichLog`` parses them via ``Text.from_ansi`` and renders
    them with ``wrap=True`` at the widget's actual width. highlight=False
    prevents Rich from auto-detecting URLs/paths and emitting OSC hyperlink
    sequences, which Textual does not sanitize and which corrupt the
    display on terminal resize.
    """
    from rich.console import Console

    console = Console(
        file=_StreamFile(emitter),
        force_terminal=True,
        color_system="truecolor",
        highlight=False,
        width=120,
    )
    # Stash the backing emitter so callers (e.g. _cli_emitter) can emit
    # structured events (agent_thinking) through the same global channel.
    console._jig_event_emitter = emitter  # type: ignore[attr-defined]
    return console
