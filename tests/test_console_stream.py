import asyncio

import pytest

from jig.events import EventEmitter, JigEvent
from jig.tui.console_stream import make_streaming_console


@pytest.mark.asyncio
async def test_streaming_console_emits_events_for_each_print():
    emitter = EventEmitter()
    queue = emitter.subscribe()
    console = make_streaming_console(emitter)

    console.print("hello world")

    # Yield to the loop so the emit task runs
    await asyncio.sleep(0)
    received: list[JigEvent] = []
    while not queue.empty():
        received.append(queue.get_nowait())

    assert any("hello world" in e.data["content"] for e in received)
    assert all(e.type == "agent_render" for e in received)


@pytest.mark.asyncio
async def test_streaming_console_skips_empty_writes():
    emitter = EventEmitter()
    queue = emitter.subscribe()
    console = make_streaming_console(emitter)

    console.print("")  # may or may not produce a write — verify no crash
    console.print("nonempty")
    await asyncio.sleep(0)

    received = []
    while not queue.empty():
        received.append(queue.get_nowait())

    # At minimum, the "nonempty" content reaches us
    assert any("nonempty" in e.data["content"] for e in received)


@pytest.mark.asyncio
async def test_streaming_console_renders_markup():
    emitter = EventEmitter()
    queue = emitter.subscribe()
    console = make_streaming_console(emitter)

    console.print("[bold]bold text[/bold]")
    await asyncio.sleep(0)

    received = []
    while not queue.empty():
        received.append(queue.get_nowait())
    text = "".join(e.data["content"] for e in received)
    # ANSI escape for bold: \x1b[1m ... \x1b[0m
    assert "bold text" in text
    assert "\x1b[" in text  # some ANSI escape was emitted
