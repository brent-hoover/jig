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

import pytest

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


async def test_record_then_replay_reproduces_the_run() -> None:
    # The full chain an operator uses: record(factory, ctx) -> RecordedRunAgent.
    # The fake agent deterministically emits _EVENTS and returns _RESULT, so a
    # consumer of the replay observes exactly what the live run produced.
    recording = await record(
        lambda em: _FakeEmittingAgent(em, _EVENTS, _RESULT), ctx=object()
    )

    replay_emitter = EventEmitter()
    replay_queue = replay_emitter.subscribe()
    replay_result = await RecordedRunAgent(recording, replay_emitter)(ctx=object())
    observed = _drain(replay_queue)

    assert observed == _EVENTS  # the live stream, reproduced
    assert replay_result == _RESULT  # the live result, reproduced exactly


async def test_replay_preserves_a_result_events_field_unchanged() -> None:
    # If a run's AgentRunResult carried its own .events, replay returns them
    # unchanged — the re-emitted stream is captured separately, not folded in.
    result = AgentRunResult(status="success", final_text="ok", events=["sentinel"])
    recording = Recording.from_result(result, _EVENTS)

    replayed = await RecordedRunAgent(recording)(ctx=object())

    assert replayed.events == ["sentinel"]  # not the _EVENTS stream
    assert tuple(recording.stream) == tuple(_EVENTS)


def test_recording_round_trips_through_a_dict_fixture() -> None:
    result = AgentRunResult(
        status="failed",
        final_text="gave up",
        total_cost_usd=0.5,
        warnings=["audit write failed"],  # exercise warnings serialization
    )
    recording = Recording.from_result(result, _EVENTS)

    again = Recording.from_dict(recording.to_dict())

    assert again == recording
    assert list(again.stream) == _EVENTS
    assert again.warnings == ("audit write failed",)


def test_from_dict_fails_loudly_on_a_malformed_fixture() -> None:
    with pytest.raises(ValueError, match="malformed Recording fixture"):
        Recording.from_dict({"final_text": "x"})  # missing "status"
