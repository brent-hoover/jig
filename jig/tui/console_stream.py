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
    """File-like sink that wraps each ``write`` call into a JigEvent
    and emits it through the EventEmitter.

    Rich's Console writes ANSI-rendered text per ``Console.print`` call
    (potentially multiple writes per call, one per segment). We emit
    each write as-is — no re-buffering — so the TUI sees content with
    minimal latency. Empty writes are skipped.
    """

    def __init__(self, emitter: "EventEmitter") -> None:
        self._emitter = emitter

    def write(self, text: str) -> int:
        if not text:
            return 0
        # The emitter's emit is async; we schedule it and don't await.
        # Console.write must be sync-safe, so use create_task on the
        # running loop. If no loop is running (e.g. called from sync
        # code outside an event loop), drop the write — this should
        # never happen in practice because the daemon owns the loop.
        from jig.events import JigEvent

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return len(text)
        loop.create_task(
            self._emitter.emit(
                JigEvent(type="agent_render", data={"content": text})
            )
        )
        return len(text)

    def flush(self) -> None:
        # Nothing buffered; rich expects flush() to exist but we
        # already emitted on each write.
        return

    def isatty(self) -> bool:
        # Force rich to render colors / ANSI for us.
        return True


def make_streaming_console(emitter: "EventEmitter"):
    """Build a rich.Console that ships its output through ``emitter``.

    ANSI escape sequences are emitted as part of the content; the
    TUI's ``RichLog`` (with markup=False) writes them out and the
    receiving terminal interprets the colors.
    """
    from rich.console import Console

    return Console(
        file=_StreamFile(emitter),
        force_terminal=True,
        color_system="truecolor",
        width=120,
        soft_wrap=True,
    )
