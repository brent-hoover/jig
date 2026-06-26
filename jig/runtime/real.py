"""RealRunAgent — the production ``RunAgent`` (Epic 3, task 3).

Wraps ``jig.agent.run_agent``, which owns the entangled spawn + sandbox + MCP
lifecycle. Bones delegates and maps the legacy ``RunAgentResult`` onto the
contract's ``AgentRunResult``; MVP routes the orchestrator's spawns through this
seam (instead of calling ``agent.py`` directly), and Final pulls the MCP/sandbox
lifecycle in as runtime-internal.

This module imports the full agent stack, so it is deliberately NOT re-exported
from ``jig.runtime`` — import ``from jig.runtime.real import RealRunAgent``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import jig.agent as agent_mod
from jig.runtime.contract import AgentRunContext, AgentRunResult

if TYPE_CHECKING:
    from jig.events import EventEmitter


class RealRunAgent:
    """A ``RunAgent`` backed by ``jig.agent.run_agent``."""

    def __init__(self, emitter: "EventEmitter | None" = None) -> None:
        self._emitter = emitter

    async def __call__(self, ctx: AgentRunContext) -> AgentRunResult:
        # Attribute access (not a bound import) so tests can monkeypatch
        # ``jig.agent.run_agent``.
        result = await agent_mod.run_agent(ctx, self._emitter)
        return AgentRunResult(
            status=result.status,
            final_text=result.final_text,
            total_cost_usd=result.total_cost_usd,
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
            warnings=list(result.warnings),
            # events left empty in Bones; populated by RecordedRunAgent in MVP.
        )
