"""Runtime bones — RealRunAgent (Epic 3, task 3).

The production seam wraps ``jig.agent.run_agent`` (spawn + sandbox + MCP). Bones
delegates and maps the legacy ``RunAgentResult`` onto ``AgentRunResult``; MVP
routes the orchestrator's spawns through it. Tested without a real spawn by
stubbing ``run_agent``.
"""

from __future__ import annotations

import jig.agent as agent_mod
from jig.agent import RunAgentResult
from jig.runtime import AgentRunResult, RunAgent
from jig.runtime.real import RealRunAgent


def test_real_satisfies_the_run_agent_protocol() -> None:
    assert isinstance(RealRunAgent(), RunAgent)


async def test_real_delegates_to_run_agent_and_maps_the_result(monkeypatch) -> None:
    captured = {}

    async def fake_run_agent(ctx, emitter=None):
        captured["ctx"] = ctx
        captured["emitter"] = emitter
        return RunAgentResult(
            status="needs_info",
            final_text="what database?",
            total_cost_usd=0.12,
            tokens_in=10,
            tokens_out=20,
            warnings=["audit write failed"],
        )

    monkeypatch.setattr(agent_mod, "run_agent", fake_run_agent)

    sentinel_ctx = object()
    sentinel_emitter = object()
    result = await RealRunAgent(emitter=sentinel_emitter)(sentinel_ctx)

    assert captured["ctx"] is sentinel_ctx
    assert captured["emitter"] is sentinel_emitter
    assert isinstance(result, AgentRunResult)
    assert result.status == "needs_info"
    assert result.final_text == "what database?"
    assert result.total_cost_usd == 0.12
    assert result.tokens_in == 10
    assert result.tokens_out == 20
    assert result.warnings == ["audit write failed"]
    assert result.events == []  # Bones leaves the recorded-events slot empty
