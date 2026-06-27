"""Agent Runtime — the ``RunAgent`` execution seam.

Extracts agent spawn/sandbox/MCP lifecycle behind a substitutable contract:
production (``RealRunAgent``), canned (``FixtureRunAgent``), and — in MVP —
recorded replay all satisfy ``RunAgent``, so the Build engine and eval harness
depend on the seam, not on ``agent.py`` directly.

The package root re-exports the spawn context (compat — ``jig/runtime.py``
became this package), the contract, the lightweight ``FixtureRunAgent``, and the
record/replay seam (``RecordedRunAgent`` + ``Recording`` + ``record``, which
import only ``jig.events``). ``RealRunAgent`` pulls in the whole agent stack and
is imported explicitly from ``jig.runtime.real`` to keep this package cheap for
its many consumers.
"""

from __future__ import annotations

from jig.runtime.contract import (
    AgentRunContext,
    AgentRunResult,
    RunAgent,
)
from jig.runtime.fixture import FixtureRunAgent
from jig.runtime.recorded import Recording, RecordedRunAgent, record
from jig.runtime.spawn_context import AgentSpawnContext, SpawnReason

__all__ = [
    "AgentRunContext",
    "AgentRunResult",
    "AgentSpawnContext",
    "FixtureRunAgent",
    "Recording",
    "RecordedRunAgent",
    "RunAgent",
    "SpawnReason",
    "record",
]
