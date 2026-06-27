"""RecordedRunAgent — record a real agent run and replay it as a fixture.

Spike 3 (#203): the agent's streaming output is the sequence of ``JigEvent``s it
emits to an ``EventEmitter`` during the run; its final state is the
``AgentRunResult``. A ``Recording`` captures both, serializably (so it persists
as a fixture). ``record()`` wraps any ``RunAgent`` — including ``RealRunAgent``,
the way you record an actual ``claude-agent-sdk`` run — with a capturing emitter.
``RecordedRunAgent`` replays the recorded events to an emitter, then returns the
recorded result, so a downstream consumer subscribed to the emitter sees an
identical stream and result (it can't tell live from replay).

Note: replay reproduces event *order/content*, not wall-clock timing — events
fire as fast as the consumer drains, which is what fixture-based evals want.

Dependency-light: imports only ``jig.events`` + the contract, so it stays
re-exportable from the ``jig.runtime`` root (no agent stack).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any

from jig.events import EventEmitter, JigEvent
from jig.runtime.contract import AgentRunResult


@dataclass(frozen=True)
class Recording:
    """A captured agent run.

    ``stream`` is the sequence of ``JigEvent``s the agent emitted during the run
    — re-emitted on replay. The result fields (incl. ``result_events``, the run's
    own ``AgentRunResult.events``) are the final state — returned *unchanged* on
    replay. The two are kept separate so a replayed result equals the live result
    exactly while the emitter stream is reproduced faithfully.
    """

    status: str
    final_text: str
    stream: tuple[JigEvent, ...] = ()
    total_cost_usd: float | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    warnings: tuple[str, ...] = ()
    result_events: tuple[object, ...] = ()

    @classmethod
    def from_result(
        cls, result: AgentRunResult, stream: Iterable[JigEvent]
    ) -> Recording:
        return cls(
            status=result.status,
            final_text=result.final_text,
            stream=tuple(stream),
            total_cost_usd=result.total_cost_usd,
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
            warnings=tuple(result.warnings),
            result_events=tuple(result.events),
        )

    def to_result(self) -> AgentRunResult:
        """The recorded result, reproduced exactly (its own ``events`` preserved;
        the emitter ``stream`` is replayed separately, not folded in here)."""
        return AgentRunResult(
            status=self.status,
            final_text=self.final_text,
            total_cost_usd=self.total_cost_usd,
            tokens_in=self.tokens_in,
            tokens_out=self.tokens_out,
            warnings=list(self.warnings),
            events=list(self.result_events),
        )

    def to_dict(self) -> dict[str, Any]:
        """A JSON-serializable form so a recording can be saved as a fixture.
        (Real runs leave ``result_events`` empty, streaming through the emitter
        instead; TODO validate JSON-serializability before that's populated.)"""
        return {
            "status": self.status,
            "final_text": self.final_text,
            "stream": [{"type": e.type, "data": e.data} for e in self.stream],
            "total_cost_usd": self.total_cost_usd,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "warnings": list(self.warnings),
            "result_events": list(self.result_events),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Recording:
        try:
            status = data["status"]
            final_text = data["final_text"]
        except KeyError as exc:
            raise ValueError(f"malformed Recording fixture: missing {exc}") from exc
        return cls(
            status=status,
            final_text=final_text,
            stream=tuple(
                JigEvent(type=e["type"], data=e["data"]) for e in data.get("stream", [])
            ),
            total_cost_usd=data.get("total_cost_usd"),
            tokens_in=data.get("tokens_in"),
            tokens_out=data.get("tokens_out"),
            warnings=tuple(data.get("warnings", [])),
            result_events=tuple(data.get("result_events", [])),
        )


class _CapturingEmitter(EventEmitter):
    """An ``EventEmitter`` that records every event it emits (while still
    forwarding to any subscribers)."""

    def __init__(self) -> None:
        super().__init__()
        self.captured: list[JigEvent] = []

    async def emit(self, event: JigEvent) -> None:
        self.captured.append(event)
        await super().emit(event)


# A factory that builds a RunAgent bound to the given emitter — e.g.
# ``lambda em: RealRunAgent(emitter=em)``.
RunAgentFactory = Callable[
    [EventEmitter], Callable[[object], Awaitable[AgentRunResult]]
]


async def record(make_run_agent: RunAgentFactory, ctx: object) -> Recording:
    """Run the agent built by ``make_run_agent`` and capture its event stream +
    result into a ``Recording``."""
    emitter = _CapturingEmitter()
    agent = make_run_agent(emitter)
    result = await agent(ctx)
    return Recording.from_result(result, emitter.captured)


class RecordedRunAgent:
    """A ``RunAgent`` that replays a ``Recording``: re-emits the recorded events
    to ``emitter`` (if given), then returns the recorded result."""

    def __init__(
        self, recording: Recording, emitter: EventEmitter | None = None
    ) -> None:
        self._recording = recording
        self._emitter = emitter

    async def __call__(self, ctx: object) -> AgentRunResult:
        if self._emitter is not None:
            for event in self._recording.stream:
                await self._emitter.emit(event)
        return self._recording.to_result()


__all__ = [
    "Recording",
    "RecordedRunAgent",
    "RunAgentFactory",
    "record",
]
