"""Build engine bones — the Supervisor (Epic 4, task 4).

The Supervisor translates the existing detectors' signals (stall, deadlock
nudge/escalation) into supervisory *events*. It never mutates tickets — emitting
events is its whole job; ``decide``/``dispatch`` act on them. Bones defines the
boundary + event vocabulary; MVP feeds it the real ``StallDetector`` /
``sweep_blocking_entries`` outputs and routes the events into ``decide``.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass

from jig.engines.build.supervisor import (
    DeadlockEscalationNeeded,
    DeadlockNudgeNeeded,
    StallDetected,
    Supervisor,
)


@dataclass(frozen=True)
class _FakeVerdict:
    signal: str
    detail: str


def test_observe_is_synchronous() -> None:
    # The supervisor only translates signals — no I/O, no await.
    assert not inspect.iscoroutinefunction(Supervisor.observe)


def test_translates_a_stall_verdict_into_a_stall_event() -> None:
    events = Supervisor().observe(
        stall=_FakeVerdict(signal="heartbeat_gap", detail="no pulse for 90s")
    )
    assert events == [StallDetected(signal="heartbeat_gap", detail="no pulse for 90s")]


def test_translates_deadlock_nudges_and_escalations() -> None:
    events = Supervisor().observe(nudged=["entry-1"], escalated=["entry-2"])
    assert events == [
        DeadlockNudgeNeeded(entry_id="entry-1"),
        DeadlockEscalationNeeded(entry_id="entry-2"),
    ]


def test_no_signals_yields_no_events() -> None:
    assert Supervisor().observe() == []
