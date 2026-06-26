"""Build engine — pure ``decide()`` core + async dispatch shell + supervisor.

The god-object split (Epic 4): ``decide`` (functional core, pure), ``dispatch``
(imperative shell, async), ``supervisor`` (emits events, never mutates), and the
thin ``BuildCoordinator`` that composes them. Bones covers the happy-path ticket
lifecycle with shimmed effect handlers; MVP migrates real transitions and wires
the coordinator into ``orchestrator.py``, which becomes a facade.
"""

from __future__ import annotations

from jig.engines.build.coordinator import BuildCoordinator
from jig.engines.build.decide import (
    AgentCompleted,
    AgentSucceeded,
    BuildPhase,
    BuildState,
    EngineState,
    MergeWorktree,
    PublishCompleted,
    SpawnAgent,
    TicketReady,
    UnblockDependents,
    WorktreeMerged,
    decide,
)
from jig.engines.build.dispatch import Dispatcher, UnhandledActionError
from jig.engines.build.supervisor import (
    DeadlockEscalationNeeded,
    DeadlockNudgeNeeded,
    StallDetected,
    Supervisor,
    SupervisoryEvent,
)

__all__ = [
    "AgentCompleted",
    "AgentSucceeded",
    "BuildCoordinator",
    "BuildPhase",
    "BuildState",
    "DeadlockEscalationNeeded",
    "DeadlockNudgeNeeded",
    "Dispatcher",
    "EngineState",
    "MergeWorktree",
    "PublishCompleted",
    "SpawnAgent",
    "StallDetected",
    "Supervisor",
    "SupervisoryEvent",
    "TicketReady",
    "UnblockDependents",
    "UnhandledActionError",
    "WorktreeMerged",
    "decide",
]
