"""Spike 3 (#203) — agent record/replay through the RunAgent seam.

Question: can we record a real agent's streaming output and replay it through the
``RunAgent`` seam as a fixture, such that a downstream consumer can't tell the
difference?

Streaming happens through the ``EventEmitter`` (JigEvents) the agent emits to
during a run; the final state is the ``AgentRunResult``. A ``Recording`` captures
both. ``record()`` wraps any RunAgent (incl. ``RealRunAgent``) with a capturing
emitter; ``RecordedRunAgent`` replays the events then returns the result.

These tests use a deterministic fake emitting agent (no live spawn) to prove the
mechanism. Recording an actual ``claude-agent-sdk`` run is then just
``record(lambda em: RealRunAgent(emitter=em), ctx)`` — an operator action.
"""

from __future__ import annotations

import asyncio

from jig.events import EventEmitter, JigEvent
from jig.runtime import (
    AgentRunResult,
    Recording,
    RecordedRunAgent,
    RunAgent,
    record,
)


class _FakeEmittingAgent:
    """A RunAgent that streams a fixed JigEvent sequence then returns a result —
    stands in for a real agent run."""

    def __init__(
        self, emitter: EventEmitter, events: list[JigEvent], result: AgentRunResult
    ) -> None:
        self._emitter = emitter
        self._events = events
        self._result = result

    async def __call__(self, ctx: object) -> AgentRunResult:
        for event in self._events:
            await self._emitter.emit(event)
        return self._result


def _drain(queue: asyncio.Queue[JigEvent]) -> list[JigEvent]:
    events: list[JigEvent] = []
    while not queue.empty():
        events.append(queue.get_nowait())
    return events


_EVENTS = [
    JigEvent(type="agent_thinking", data={"text": "considering"}),
    JigEvent(type="tool_use", data={"tool": "Read", "path": "x.py"}),
    JigEvent(type="agent_message", data={"text": "done"}),
]
_RESULT = AgentRunResult(
    status="success", final_text="done", tokens_in=10, tokens_out=20
)


async def test_record_captures_the_event_stream_and_result() -> None:
    recording = await record(
        lambda em: _FakeEmittingAgent(em, _EVENTS, _RESULT), ctx=object()
    )

    assert list(recording.stream) == _EVENTS
    assert recording.status == "success"
    assert recording.final_text == "done"


def test_recorded_run_agent_satisfies_the_protocol() -> None:
    assert isinstance(
        RecordedRunAgent(Recording(status="success", final_text="")), RunAgent
    )


async def test_replay_returns_the_recorded_result() -> None:
    recording = Recording(
        status="blocked", final_text="need info", stream=tuple(_EVENTS)
    )

    result = await RecordedRunAgent(recording)(ctx=object())

    assert result.status == "blocked"
    assert result.final_text == "need info"


async def test_a_consumer_cannot_tell_live_from_replay() -> None:
    # Live: a capturing emitter both records the run AND forwards to a subscribed
    # consumer.
    live_emitter = EventEmitter()
    # record() builds its own capturing emitter; to observe the live stream too,
    # run the agent against an emitter the consumer is subscribed to.
    live_queue = live_emitter.subscribe()
    agent = _FakeEmittingAgent(live_emitter, _EVENTS, _RESULT)
    live_result = await agent(ctx=object())
    observed_live = _drain(live_queue)

    recording = Recording.from_result(live_result, _EVENTS)

    # Replay: a fresh emitter + consumer; the recorded agent re-emits.
    replay_emitter = EventEmitter()
    replay_queue = replay_emitter.subscribe()
    replay_result = await RecordedRunAgent(recording, replay_emitter)(ctx=object())
    observed_replay = _drain(replay_queue)

    assert observed_replay == observed_live  # identical stream
    assert replay_result == live_result  # identical result (incl. .events field)


async def test_replay_preserves_a_result_events_field_unchanged() -> None:
    # If a run's AgentRunResult carried its own .events, replay returns them
    # unchanged — the re-emitted stream is captured separately, not folded in.
    result = AgentRunResult(status="success", final_text="ok", events=["sentinel"])
    recording = Recording.from_result(result, _EVENTS)

    replayed = await RecordedRunAgent(recording)(ctx=object())

    assert replayed.events == ["sentinel"]  # not the _EVENTS stream
    assert tuple(recording.stream) == tuple(_EVENTS)


def test_recording_round_trips_through_a_dict_fixture() -> None:
    recording = Recording.from_result(_RESULT, _EVENTS)

    again = Recording.from_dict(recording.to_dict())

    assert again == recording
    assert list(again.stream) == _EVENTS
