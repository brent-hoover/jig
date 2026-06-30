"""Phase 5 Task P — evaluator=completing-actor conflict.

The self-certification guard at thread_mcp.py lines ~1380–1385
raises ``ThreadError("evaluator cannot be the completing actor")``
when an attempted accept's sender equals the handoff author.

This integration test wires a workflow where the phase evaluator
resolves to the same role that completes the phase, so the guard
fires via the full orchestrator → evaluator-spawn → MCP-accept
path. Expected outcome: the handoff stays pending, the next phase
never runs, and the ThreadError is visible to the fake agent.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from jig.models import (
    PhaseConfig,
    RoleConfig,
    SpecificRoleEvaluator,
    WorkflowConfig,
)
from jig.thread import Handoff
from jig.thread_mcp import ThreadError, handle_thread_accept_handoff
from jig.ticket import Ticket, WorkType
from tests._phase5p_helpers import build_orch, poll_until
from tests._test_ticket import TICKET_AC_PLACEHOLDER


@pytest.mark.asyncio
async def test_evaluator_equal_to_completing_role_does_not_advance(
    tmp_path: Path, monkeypatch
) -> None:
    workflow = WorkflowConfig(
        name="default",
        phases=[
            PhaseConfig(
                name="spec",
                role="spec",
                evaluator=SpecificRoleEvaluator(type="specific_role", role="spec"),
            ),
            PhaseConfig(name="dev", role="dev"),
        ],
    )
    roles = [
        RoleConfig(role="spec", phase_prompt="spec"),
        RoleConfig(role="dev", phase_prompt="dev"),
    ]
    orch = build_orch(
        tmp_path,
        workflow=workflow,
        roles=roles,
        monkeypatch=monkeypatch,
    )

    from jig.agent import RunAgentResult
    from jig.runtime import SpawnReason

    run_calls: list[str] = []
    accept_errors: list[Exception] = []

    async def fake_run_agent(ctx, emitter=None):
        run_calls.append(ctx.role)
        if ctx.spawn_reason == SpawnReason.EVALUATOR:
            # Dispatch path: evaluator spawn. Pull the handoff id out
            # of the spawn message and try to accept — guard should fire.
            hid: str | None = None
            if ctx.initial_bus_message is not None:
                hid = ctx.initial_bus_message.get("handoff_id")
            if hid is None:
                handoffs = await ctx.threads.find_by_kind(ctx.ticket.id, "handoff")
                hid = handoffs[-1].id if handoffs else None
            assert hid is not None
            try:
                await handle_thread_accept_handoff(
                    tickets=ctx.tickets,
                    threads=ctx.threads,
                    bus=ctx.bus,
                    sender="spec",  # same role as handoff author
                    args={"handoff_id": hid},
                    project_path=tmp_path,
                )
            except ThreadError as e:
                accept_errors.append(e)
        elif ctx.role == "spec":
            # Completing spawn: post the pending handoff authored by "spec".
            await ctx.threads.post(
                Handoff(
                    ticket_id=ctx.ticket.id,
                    author="spec",
                    phase="spec",
                    outputs=["spec.md"],
                    summary="draft",
                )
            )
        elif ctx.role == "dev":
            # Would only run if the guard failed. The final
            # ``assert "dev" not in run_calls`` surfaces this case —
            # raising here would be swallowed by the orchestrator's
            # task-done cleanup callback.
            pass
        return RunAgentResult(status="success", final_text="ok")

    monkeypatch.setattr("jig.agent.run_agent", fake_run_agent)

    await orch.startup()
    try:
        tid = await orch.tickets.create(
            Ticket(
                work_type=WorkType.FEATURE,
                title="f",
                created_by="user",
                description=TICKET_AC_PLACEHOLDER,
            )
        )
        await orch._handle_schedule(tid)

        # Wait for the evaluator spawn to fire and record an error.
        async def evaluator_attempted() -> bool:
            return bool(accept_errors)

        assert await poll_until(evaluator_attempted, timeout_s=3.0), (
            f"evaluator never attempted accept; run_calls={run_calls}"
        )

        # Give the orchestrator time to (incorrectly) advance if the
        # guard somehow let the accept through.
        await asyncio.sleep(0.3)

        # Exactly the ThreadError we expect.
        assert any(
            "evaluator cannot be the completing actor" in str(e) for e in accept_errors
        ), f"unexpected errors: {accept_errors!r}"

        # Handoff stays pending — no accept landed.
        handoffs = await orch.threads.find_by_kind(tid, "handoff")
        assert len(handoffs) == 1
        assert isinstance(handoffs[0], Handoff)
        assert handoffs[0].acceptance_state == "pending"

        # Dev phase never ran.
        assert "dev" not in run_calls, f"dev should not run; got {run_calls}"
    finally:
        await orch.shutdown()
