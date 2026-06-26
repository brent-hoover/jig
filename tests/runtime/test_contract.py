"""Runtime bones — the RunAgent contract (Epic 3, task 2).

The seam: a ``RunAgent`` is anything callable as ``async (ctx) -> AgentRunResult``.
``AgentRunContext`` is the existing spawn context (ticket/role/worktree/MCP
wiring); ``AgentRunResult`` carries exit status + artifacts.
"""

from __future__ import annotations

from jig.runtime import AgentRunContext, AgentRunResult, RunAgent
from jig.runtime.spawn_context import AgentSpawnContext


def test_agent_run_context_is_the_spawn_context() -> None:
    # Bones: the run context is the existing spawn context, named for the seam.
    assert AgentRunContext is AgentSpawnContext


def test_agent_run_result_carries_status_and_artifacts() -> None:
    r = AgentRunResult(status="success", final_text="done")
    assert r.status == "success"
    assert r.final_text == "done"
    assert r.warnings == []
    assert r.events == []


def test_run_agent_is_a_runtime_checkable_protocol() -> None:
    class _Stub:
        async def __call__(self, ctx: AgentRunContext) -> AgentRunResult:
            return AgentRunResult(status="success", final_text="")

    assert isinstance(_Stub(), RunAgent)

    class _NotCallable:
        pass

    assert not isinstance(_NotCallable(), RunAgent)
