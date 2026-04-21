"""Tests for the per-ticket loop in Orchestrator."""
import asyncio
from pathlib import Path

import pytest

from jig.models import RoleConfig, PhaseConfig, WorkflowConfig
from jig.orchestrator import Orchestrator
from jig.project import Project, save_project
from jig.thread import SystemEvent
from jig.ticket import Ticket, TicketStatus, WorkType


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
    from jig.persistence import save_role, save_workflow
    (tmp_path / ".jig" / "workflows").mkdir(parents=True)
    (tmp_path / ".jig" / "roles").mkdir()
    save_workflow(tmp_path, wf)
    for role in ("spec-writer", "dev", "qa"):
        save_role(
            tmp_path, RoleConfig(role=role, phase_prompt=f"be {role}")
        )

    orch = Orchestrator(project_path=tmp_path)

    from jig import orchestrator as orch_module
    from jig.agent import RunAgentResult

    run_calls: list[str] = []

    async def fake_run_agent(ctx, emitter=None):
        run_calls.append(ctx.role)
        await ctx.tickets.update_status(ctx.ticket.id, TicketStatus.RESOLVED)
        return RunAgentResult(status="success", final_text="ok")

    monkeypatch.setattr(orch_module, "run_agent", fake_run_agent)

    async def fake_ensure(ticket):
        return tmp_path / "worktree"

    orch._ensure_worktree = fake_ensure  # type: ignore[method-assign]

    # C3: `_on_ticket_completed` runs merge before marking RESOLVED; with
    # a fake worktree there's no real git repo, so stub out the merge.
    async def fake_merge(*args, **kwargs):
        return "stub-merge"

    monkeypatch.setattr(
        "jig.worktree.merge_ticket", fake_merge
    )

    async def fake_remove(*args, **kwargs):
        return None

    monkeypatch.setattr(
        "jig.worktree.remove_worktree", fake_remove
    )

    await orch.startup()
    try:
        tid = await orch.tickets.create(
            Ticket(work_type=WorkType.FEATURE, title="f", created_by="user")
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


# ---------------------------------------------------------------------------
# Helpers shared by the two new tests
# ---------------------------------------------------------------------------


def _make_project_and_workflow(
    tmp_path: Path,
    phase_names: list[str],
) -> WorkflowConfig:
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
    phases = [PhaseConfig(name=n, role=f"role-{n}") for n in phase_names]
    wf = WorkflowConfig(name="default", phases=phases)
    from jig.persistence import save_role, save_workflow

    (tmp_path / ".jig" / "workflows").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".jig" / "roles").mkdir(parents=True, exist_ok=True)
    save_workflow(tmp_path, wf)
    for n in phase_names:
        save_role(
            tmp_path, RoleConfig(role=f"role-{n}", phase_prompt=f"be {n}")
        )
    return wf


# ---------------------------------------------------------------------------
# Test 1: run_agent exception marks both child task and parent ticket FAILED
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_agent_exception_marks_ticket_failed(
    tmp_path: Path, monkeypatch
) -> None:
    _make_project_and_workflow(tmp_path, ["spec"])

    orch = Orchestrator(project_path=tmp_path)

    from jig import orchestrator as orch_module

    async def exploding_run_agent(ctx, emitter=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(orch_module, "run_agent", exploding_run_agent)

    async def fake_ensure(ticket):
        return tmp_path / "worktree"

    orch._ensure_worktree = fake_ensure  # type: ignore[method-assign]

    await orch.startup()
    try:
        tid = await orch.tickets.create(
            Ticket(work_type=WorkType.FEATURE, title="feat", created_by="user")
        )
        await orch._handle_schedule(tid)
        # Wait for the asyncio task to finish
        running_task = orch._running_tickets.get(tid)
        if running_task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(running_task), timeout=2.0)
            except (asyncio.TimeoutError, Exception):
                pass

        ticket = await orch.tickets.get(tid)
        assert ticket is not None
        assert ticket.status == TicketStatus.FAILED, (
            f"expected FAILED, got {ticket.status}"
        )
    finally:
        await orch.shutdown()


# ---------------------------------------------------------------------------
# Test 2: _current_phase_index counts by phase name, not by task-ticket count
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_current_phase_index_skips_by_phase_name_not_task_count(
    tmp_path: Path,
) -> None:
    """Duplicate phase_run comments for the same phase should count as one phase done."""
    wf = _make_project_and_workflow(tmp_path, ["a", "b", "c"])

    orch = Orchestrator(project_path=tmp_path)
    await orch.startup()
    try:
        ticket_id = await orch.tickets.create(
            Ticket(work_type=WorkType.FEATURE, title="feat", created_by="user")
        )

        # Post TWO phase_run system events for phase "a" (simulating a retry)
        for i in range(2):
            await orch.threads.post(
                SystemEvent(
                    ticket_id=ticket_id,
                    author="orchestrator",
                    event_type="phase_run",
                    content=f"phase a: success (attempt {i})",
                    phase_result="success",
                )
            )

        result = await orch._current_phase_index(ticket_id, wf)

        # Phase "a" is done but that's still only 1 phase.
        # The correct answer is 1 (only phase "a" is complete; start at "b").
        assert result == 1, (
            f"expected 1 (only phase 'a' done), got {result}"
        )
    finally:
        await orch.shutdown()
