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

from jig.events import EventEmitter, JigEvent
from jig.runtime.contract import AgentRunResult


@dataclass(frozen=True)
class Recording:
    """A captured agent run: the emitted event stream + the final result."""

    status: str
    final_text: str
    events: tuple[JigEvent, ...] = ()
    total_cost_usd: float | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    warnings: tuple[str, ...] = ()

    @classmethod
    def from_result(
        cls, result: AgentRunResult, events: Iterable[JigEvent]
    ) -> Recording:
        return cls(
            status=result.status,
            final_text=result.final_text,
            events=tuple(events),
            total_cost_usd=result.total_cost_usd,
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
            warnings=tuple(result.warnings),
        )

    def to_result(self) -> AgentRunResult:
        """The seam result this recording replays, with ``events`` populated."""
        return AgentRunResult(
            status=self.status,
            final_text=self.final_text,
            total_cost_usd=self.total_cost_usd,
            tokens_in=self.tokens_in,
            tokens_out=self.tokens_out,
            warnings=list(self.warnings),
            events=list(self.events),
        )

    def to_dict(self) -> dict:
        """A JSON-serializable form so a recording can be saved as a fixture."""
        return {
            "status": self.status,
            "final_text": self.final_text,
            "events": [{"type": e.type, "data": e.data} for e in self.events],
            "total_cost_usd": self.total_cost_usd,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_dict(cls, data: dict) -> Recording:
        return cls(
            status=data["status"],
            final_text=data["final_text"],
            events=tuple(
                JigEvent(type=e["type"], data=e["data"]) for e in data.get("events", [])
            ),
            total_cost_usd=data.get("total_cost_usd"),
            tokens_in=data.get("tokens_in"),
            tokens_out=data.get("tokens_out"),
            warnings=tuple(data.get("warnings", [])),
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
            for event in self._recording.events:
                await self._emitter.emit(event)
        return self._recording.to_result()


__all__ = [
    "Recording",
    "RecordedRunAgent",
    "record",
]
