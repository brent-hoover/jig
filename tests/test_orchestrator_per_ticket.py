"""Tests for the per-ticket loop in Orchestrator."""
import asyncio
from pathlib import Path

import pytest

from jig.models import AgentTypeConfig, PhaseConfig, WorkflowConfig
from jig.orchestrator import Orchestrator
from jig.project import Project, save_project
from jig.ticket import Ticket, TicketStatus, TicketType


@pytest.mark.asyncio
async def test_per_ticket_loop_walks_phases_to_resolved(
    tmp_path: Path, monkeypatch
) -> None:
    save_project(
        tmp_path,
        Project(
            id="p",
            name="p",
            path=str(tmp_path),
            language="python",
            package_manager="uv",
        ),
    )

    wf = WorkflowConfig(
        name="default",
        phases=[
            PhaseConfig(name="spec", role="spec-writer"),
            PhaseConfig(name="dev", role="dev"),
            PhaseConfig(name="qa", role="qa"),
        ],
    )
    from jig.persistence import save_agent_type, save_workflow
    (tmp_path / ".jig" / "workflows").mkdir(parents=True)
    (tmp_path / ".jig" / "agent_types").mkdir()
    save_workflow(tmp_path, wf)
    for role in ("spec-writer", "dev", "qa"):
        save_agent_type(
            tmp_path, AgentTypeConfig(role=role, phase_prompt=f"be {role}")
        )

    orch = Orchestrator(project_path=tmp_path)

    from jig import orchestrator as orch_module
    from jig.agent import RunAgentResult

    run_calls: list[str] = []

    async def fake_run_agent(ctx):
        run_calls.append(ctx.role)
        await ctx.tickets.update_status(ctx.ticket.id, TicketStatus.RESOLVED)
        return RunAgentResult(status="success", final_text="ok")

    monkeypatch.setattr(orch_module, "run_agent", fake_run_agent)

    async def fake_ensure(ticket):
        return tmp_path / "worktree"

    orch._ensure_worktree = fake_ensure  # type: ignore[method-assign]

    await orch.startup()
    try:
        tid = await orch.tickets.create(
            Ticket(type=TicketType.FEATURE, title="f", created_by="user")
        )
        await orch._handle_schedule(tid)
        for _ in range(40):
            await asyncio.sleep(0.05)
            current = await orch.tickets.get(tid)
            if current and current.status == TicketStatus.RESOLVED:
                break
        assert run_calls == ["spec-writer", "dev", "qa"]
        final = await orch.tickets.get(tid)
        assert final is not None
        assert final.status == TicketStatus.RESOLVED
    finally:
        await orch.shutdown()
