"""The ``RunAgent`` contract — the substitutable agent-execution seam.

A ``RunAgent`` is anything callable as ``async (ctx) -> AgentRunResult``. This is
the evaluability pivot: production spawns (``RealRunAgent``), canned fixtures
(``FixtureRunAgent``), and — in MVP — recorded replays all satisfy it, so the
Build engine and eval harness depend on the contract, not on ``agent.py``.

Kept dependency-light on purpose: this module imports only the spawn context, so
``jig.runtime`` stays cheap to import for the 15 existing consumers. The
production implementation (which pulls in the whole agent stack) lives in
``jig.runtime.real`` and is imported explicitly, never from the package root.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from jig.runtime.spawn_context import AgentSpawnContext

# Bones: the run context is the existing spawn context, named for the seam.
# MVP/Final may promote it to a first-class runtime type.
AgentRunContext = AgentSpawnContext


@dataclass
class AgentRunResult:
    """What a ``RunAgent`` returns: exit status + artifacts.

    Mirrors ``jig.agent.RunAgentResult`` (the legacy spawn result) and adds an
    ``events`` slot for the recorded/replay seam — empty in bones, populated by
    ``RecordedRunAgent`` in MVP/Final.
    """

    status: str  # "success" | "failed" | "blocked" | "needs_info"
    final_text: str
    total_cost_usd: float | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    warnings: list[str] = field(default_factory=list)
    events: list[Any] = field(default_factory=list)


@runtime_checkable
class RunAgent(Protocol):
    """The agent-execution seam."""

    async def __call__(self, ctx: AgentRunContext) -> AgentRunResult: ...
