"""Epic 3 MVP task 2 — the orchestrator spawns through the RunAgent seam.

The orchestrator no longer calls ``jig.agent.run_agent`` directly; every spawn
goes through ``self._run_agent`` (a ``RunAgent``). The default wraps the real
spawn path (``RealRunAgent``); headless evals inject a Fixture/Recorded one via
the ``run_agent=`` constructor param. (Full end-to-end routing is covered by the
existing orchestrator suite, which only intercepts spawns by patching
``jig.agent.run_agent`` — i.e. through this seam.)
"""

from __future__ import annotations

from jig.orchestrator import Orchestrator
from jig.runtime.contract import AgentRunResult
from jig.runtime.fixture import FixtureRunAgent
from jig.runtime.real import RealRunAgent


def test_default_run_agent_is_the_real_seam(tmp_path) -> None:
    orch = Orchestrator(project_path=tmp_path)
    assert isinstance(orch._run_agent, RealRunAgent)


def test_injected_run_agent_is_used(tmp_path) -> None:
    fixture = FixtureRunAgent()
    orch = Orchestrator(project_path=tmp_path, run_agent=fixture)
    assert orch._run_agent is fixture


async def test_seam_returns_the_injected_agents_result(tmp_path) -> None:
    canned = AgentRunResult(status="success", final_text="from fixture")
    fixture = FixtureRunAgent(result=canned)
    orch = Orchestrator(project_path=tmp_path, run_agent=fixture)

    ctx = object()  # FixtureRunAgent is context-agnostic
    result = await orch._run_agent(ctx)

    assert result is canned
    assert fixture.calls == [ctx]
