"""Build engine — the pure ``decide()`` core + (later) dispatch/supervisor shell.

Spike 1 (#201) seeds this package with the proven-pure ``decide()`` state
machine. Epic 4 (Build) bones grows it into the full ticket lifecycle, the
data-driven transition table, the async dispatch shell, and the supervisor.
"""

from __future__ import annotations

from jig.engines.build.decide import (
    AgentCompleted,
    BuildState,
    SpawnAgent,
    TicketReady,
    decide,
)

__all__ = [
    "AgentCompleted",
    "BuildState",
    "SpawnAgent",
    "TicketReady",
    "decide",
]
