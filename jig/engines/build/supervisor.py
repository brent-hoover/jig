"""The Supervisor — turns detector signals into supervisory events.

Deadlock and stall detection live in ``jig/deadlock.py`` and
``jig/stall_detector.py``. Today they act directly (post nudges, flip tickets to
NEEDS_INFO). The target architecture inverts that: the Supervisor *observes* and
emits events, and ``decide``/``dispatch`` decide what to do — so supervision
never mutates tickets behind the state machine's back.

Bones defines the boundary + the event vocabulary and translates detector
outputs into events. It is dependency-light (duck-typed inputs, no store
imports); MVP feeds it the real ``StallDetector.check()`` verdict and
``sweep_blocking_entries`` result, and routes the events into ``decide``.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol


class _StallVerdictLike(Protocol):
    signal: str
    detail: str


# ---------------------------------------------------------------------------
# Supervisory events — emitted, never executed by the Supervisor itself.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StallDetected:
    signal: str
    detail: str


@dataclass(frozen=True)
class DeadlockNudgeNeeded:
    entry_id: str


@dataclass(frozen=True)
class DeadlockEscalationNeeded:
    entry_id: str


SupervisoryEvent = StallDetected | DeadlockNudgeNeeded | DeadlockEscalationNeeded


class Supervisor:
    """Translates detector signals into supervisory events. Never mutates."""

    def observe(
        self,
        *,
        stall: _StallVerdictLike | None = None,
        nudged: Iterable[str] = (),
        escalated: Iterable[str] = (),
    ) -> list[SupervisoryEvent]:
        events: list[SupervisoryEvent] = []
        if stall is not None:
            events.append(StallDetected(signal=stall.signal, detail=stall.detail))
        events.extend(DeadlockNudgeNeeded(entry_id=e) for e in nudged)
        events.extend(DeadlockEscalationNeeded(entry_id=e) for e in escalated)
        return events


__all__: Sequence[str] = [
    "DeadlockEscalationNeeded",
    "DeadlockNudgeNeeded",
    "StallDetected",
    "Supervisor",
    "SupervisoryEvent",
]
