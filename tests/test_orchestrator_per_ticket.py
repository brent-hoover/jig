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
        save_role(tmp_path, RoleConfig(role=role, phase_prompt=f"be {role}"))

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

    monkeypatch.setattr("jig.worktree.merge_ticket", fake_merge)

    async def fake_remove(*args, **kwargs):
        return None

    async def fake_commit(*args, **kwargs):
        return None

    monkeypatch.setattr("jig.worktree.remove_worktree", fake_remove)
    monkeypatch.setattr("jig.worktree.commit_worktree", fake_commit)

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
        save_role(tmp_path, RoleConfig(role=f"role-{n}", phase_prompt=f"be {n}"))
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
        assert result == 1, f"expected 1 (only phase 'a' done), got {result}"
    finally:
        await orch.shutdown()


# ---------------------------------------------------------------------------
# C3: merge conflict routes to MERGE_CONFLICT, not RESOLVED
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_merge_conflict_routes_to_merge_conflict_status(
    tmp_path: Path, monkeypatch
) -> None:
    """When the final merge raises ``MergeConflictError``, the ticket
    is set to MERGE_CONFLICT (not RESOLVED, not FAILED) and the branch
    + worktree are preserved for manual resolution."""
    _make_project_and_workflow(tmp_path, ["spec"])

    orch = Orchestrator(project_path=tmp_path)

    from jig import orchestrator as orch_module
    from jig.agent import RunAgentResult
    from jig.worktree import MergeConflictError

    async def fake_run_agent(ctx, emitter=None):
        return RunAgentResult(status="success", final_text="ok")

    monkeypatch.setattr(orch_module, "run_agent", fake_run_agent)

    async def fake_ensure(ticket):
        return tmp_path / "worktree"

    orch._ensure_worktree = fake_ensure  # type: ignore[method-assign]

    async def fake_try_resolve(ticket_id, ticket):
        return False

    orch._try_resolve_conflict = fake_try_resolve  # type: ignore[method-assign]

    async def conflicting_merge(project_path, ticket_id, base, strategy):
        raise MergeConflictError(ticket_id, f"jig/{ticket_id}")

    remove_calls: list[str] = []

    async def fake_remove(*args, **kwargs):
        remove_calls.append("called")

    async def fake_commit(*args, **kwargs):
        return None

    monkeypatch.setattr("jig.worktree.merge_ticket", conflicting_merge)
    monkeypatch.setattr("jig.worktree.remove_worktree", fake_remove)
    monkeypatch.setattr("jig.worktree.commit_worktree", fake_commit)

    await orch.startup()
    try:
        tid = await orch.tickets.create(
            Ticket(work_type=WorkType.FEATURE, title="f", created_by="user")
        )
        await orch._handle_schedule(tid)
        running_task = orch._running_tickets.get(tid)
        if running_task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(running_task), timeout=2.0)
            except (asyncio.TimeoutError, Exception):
                pass

        ticket = await orch.tickets.get(tid)
        assert ticket is not None
        assert ticket.status == TicketStatus.MERGE_CONFLICT, (
            f"expected MERGE_CONFLICT, got {ticket.status}"
        )
        # Worktree is preserved on conflict so a human can resolve it.
        assert remove_calls == [], (
            f"expected worktree preserved, got remove_calls={remove_calls}"
        )
    finally:
        await orch.shutdown()


# ---------------------------------------------------------------------------
# C4: dep branch merge failure fails the ticket + emits dep_merge_failed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dep_merge_failure_fails_ticket_and_emits_event(
    tmp_path: Path, monkeypatch
) -> None:
    """When ``_ensure_worktree`` raises ``DependencyMergeError`` because
    a ``blocked_by`` branch can't merge cleanly, the ticket goes to
    FAILED and a ``dep_merge_failed`` SystemEvent is posted on the
    thread. The agent never runs — we'd be running against an
    inconsistent tree."""
    _make_project_and_workflow(tmp_path, ["spec"])

    orch = Orchestrator(project_path=tmp_path)

    from jig import orchestrator as orch_module
    from jig.orchestrator import DependencyMergeError

    run_calls: list[str] = []

    async def fake_run_agent(ctx, emitter=None):
        run_calls.append(ctx.role)
        from jig.agent import RunAgentResult

        return RunAgentResult(status="success", final_text="ok")

    monkeypatch.setattr(orch_module, "run_agent", fake_run_agent)

    async def failing_ensure(ticket):
        raise DependencyMergeError(
            ticket_id=ticket.id,
            dep_id="dep-123",
            dep_branch="jig/dep-123",
        )

    orch._ensure_worktree = failing_ensure  # type: ignore[method-assign]

    await orch.startup()
    try:
        tid = await orch.tickets.create(
            Ticket(work_type=WorkType.FEATURE, title="f", created_by="user")
        )
        await orch._handle_schedule(tid)
        running_task = orch._running_tickets.get(tid)
        if running_task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(running_task), timeout=2.0)
            except (asyncio.TimeoutError, Exception):
                pass

        ticket = await orch.tickets.get(tid)
        assert ticket is not None
        assert ticket.status == TicketStatus.FAILED
        assert run_calls == [], (
            f"agent must not run after dep-merge failure, got {run_calls}"
        )

        entries = await orch.threads.for_ticket(tid)
        dep_events = [
            e
            for e in entries
            if isinstance(e, SystemEvent) and e.event_type == "dep_merge_failed"
        ]
        assert len(dep_events) == 1
        assert "dep-123" in dep_events[0].content
        assert "jig/dep-123" in dep_events[0].content
    finally:
        await orch.shutdown()


# ---------------------------------------------------------------------------
# C5: _try_resolve_conflict — unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_try_resolve_conflict_returns_false_when_role_missing(
    tmp_path: Path, monkeypatch
) -> None:
    """_try_resolve_conflict returns False (not raises) when the
    conflict_resolver role file does not exist."""
    _make_project_and_workflow(tmp_path, ["spec"])

    orch = Orchestrator(project_path=tmp_path)
    await orch.startup()
    try:
        tid = await orch.tickets.create(
            Ticket(work_type=WorkType.FEATURE, title="t", created_by="user")
        )
        ticket = await orch.tickets.get(tid)

        from jig import orchestrator as orch_module

        def raise_not_found(project_path, name):
            raise FileNotFoundError(f"role {name!r} not found")

        monkeypatch.setattr(orch_module, "load_role", raise_not_found)
        result = await orch._try_resolve_conflict(tid, ticket)
        assert result is False
    finally:
        await orch.shutdown()


@pytest.mark.asyncio
async def test_try_resolve_conflict_returns_false_when_agent_raises(
    tmp_path: Path, monkeypatch
) -> None:
    """_try_resolve_conflict returns False (not raises) when the agent spawn
    raises an unexpected exception."""
    _make_project_and_workflow(tmp_path, ["spec"])

    orch = Orchestrator(project_path=tmp_path)
    await orch.startup()
    try:
        tid = await orch.tickets.create(
            Ticket(work_type=WorkType.FEATURE, title="t", created_by="user")
        )
        ticket = await orch.tickets.get(tid)

        from jig import orchestrator as orch_module

        monkeypatch.setattr(
            orch_module,
            "load_role",
            lambda *a, **k: RoleConfig(role="conflict_resolver", phase_prompt="x"),
        )

        async def boom(ctx, spawned_by="orchestrator"):
            raise RuntimeError("agent exploded")

        orch._run_agent_with_analytics = boom  # type: ignore[method-assign]
        result = await orch._try_resolve_conflict(tid, ticket)
        assert result is False
    finally:
        await orch.shutdown()


@pytest.mark.asyncio
async def test_try_resolve_conflict_returns_true_when_agent_succeeds(
    tmp_path: Path, monkeypatch
) -> None:
    """_try_resolve_conflict returns True when the agent completes without
    raising."""
    _make_project_and_workflow(tmp_path, ["spec"])

    orch = Orchestrator(project_path=tmp_path)
    await orch.startup()
    try:
        tid = await orch.tickets.create(
            Ticket(work_type=WorkType.FEATURE, title="t", created_by="user")
        )
        ticket = await orch.tickets.get(tid)

        from jig import orchestrator as orch_module
        from jig.agent import RunAgentResult

        monkeypatch.setattr(
            orch_module,
            "load_role",
            lambda *a, **k: RoleConfig(role="conflict_resolver", phase_prompt="x"),
        )

        async def fake_run(ctx, spawned_by="orchestrator"):
            return RunAgentResult(status="success", final_text="done")

        orch._run_agent_with_analytics = fake_run  # type: ignore[method-assign]
        result = await orch._try_resolve_conflict(tid, ticket)
        assert result is True
    finally:
        await orch.shutdown()


@pytest.mark.asyncio
async def test_try_resolve_conflict_returns_false_when_agent_reports_failed(
    tmp_path: Path, monkeypatch
) -> None:
    """_try_resolve_conflict returns False when the agent completes but reports
    status='failed' (e.g., agent gave up on the conflict)."""
    _make_project_and_workflow(tmp_path, ["spec"])

    orch = Orchestrator(project_path=tmp_path)
    await orch.startup()
    try:
        tid = await orch.tickets.create(
            Ticket(work_type=WorkType.FEATURE, title="t", created_by="user")
        )
        ticket = await orch.tickets.get(tid)

        from jig import orchestrator as orch_module
        from jig.agent import RunAgentResult

        monkeypatch.setattr(
            orch_module,
            "load_role",
            lambda *a, **k: RoleConfig(role="conflict_resolver", phase_prompt="x"),
        )

        async def fake_run_failed(ctx, spawned_by="orchestrator"):
            return RunAgentResult(status="failed", final_text="gave up")

        orch._run_agent_with_analytics = fake_run_failed  # type: ignore[method-assign]
        result = await orch._try_resolve_conflict(tid, ticket)
        assert result is False
    finally:
        await orch.shutdown()


# ---------------------------------------------------------------------------
# C6: _on_ticket_completed auto-resolves merge conflicts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolver_success_routes_to_resolved(
    tmp_path: Path, monkeypatch
) -> None:
    """When _try_resolve_conflict returns True and the retry merge succeeds,
    the ticket reaches RESOLVED."""
    _make_project_and_workflow(tmp_path, ["spec"])

    orch = Orchestrator(project_path=tmp_path)

    from jig import orchestrator as orch_module
    from jig.agent import RunAgentResult
    from jig.worktree import MergeConflictError

    async def fake_run_agent(ctx, emitter=None):
        return RunAgentResult(status="success", final_text="ok")

    monkeypatch.setattr(orch_module, "run_agent", fake_run_agent)

    async def fake_ensure(ticket):
        return tmp_path / "worktree"

    orch._ensure_worktree = fake_ensure  # type: ignore[method-assign]

    call_count = 0

    async def merge_first_conflicts_then_succeeds(project_path, ticket_id, base, strategy):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise MergeConflictError(ticket_id, f"jig/{ticket_id}")
        return f"Merged jig/{ticket_id}"

    monkeypatch.setattr("jig.worktree.merge_ticket", merge_first_conflicts_then_succeeds)
    monkeypatch.setattr("jig.worktree.remove_worktree", lambda *a, **k: None)

    async def fake_commit_wt(*args, **kwargs):
        return None

    monkeypatch.setattr("jig.worktree.commit_worktree", fake_commit_wt)

    async def fake_try_resolve(ticket_id, ticket):
        return True

    orch._try_resolve_conflict = fake_try_resolve  # type: ignore[method-assign]

    await orch.startup()
    try:
        tid = await orch.tickets.create(
            Ticket(work_type=WorkType.FEATURE, title="f", created_by="user")
        )
        await orch._handle_schedule(tid)
        running_task = orch._running_tickets.get(tid)
        if running_task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(running_task), timeout=2.0)
            except (asyncio.TimeoutError, Exception):
                pass

        ticket = await orch.tickets.get(tid)
        assert ticket is not None
        assert ticket.status == TicketStatus.RESOLVED, (
            f"expected RESOLVED, got {ticket.status}"
        )
        assert call_count == 2, f"expected merge_ticket called twice, got {call_count}"
    finally:
        await orch.shutdown()


@pytest.mark.asyncio
async def test_resolver_failure_routes_to_merge_conflict(
    tmp_path: Path, monkeypatch
) -> None:
    """When _try_resolve_conflict returns False, the ticket still routes to
    MERGE_CONFLICT (existing human path unchanged)."""
    _make_project_and_workflow(tmp_path, ["spec"])

    orch = Orchestrator(project_path=tmp_path)

    from jig import orchestrator as orch_module
    from jig.agent import RunAgentResult
    from jig.worktree import MergeConflictError

    async def fake_run_agent(ctx, emitter=None):
        return RunAgentResult(status="success", final_text="ok")

    monkeypatch.setattr(orch_module, "run_agent", fake_run_agent)

    async def fake_ensure(ticket):
        return tmp_path / "worktree"

    orch._ensure_worktree = fake_ensure  # type: ignore[method-assign]

    async def always_conflicts(project_path, ticket_id, base, strategy):
        raise MergeConflictError(ticket_id, f"jig/{ticket_id}")

    monkeypatch.setattr("jig.worktree.merge_ticket", always_conflicts)
    monkeypatch.setattr("jig.worktree.remove_worktree", lambda *a, **k: None)

    async def fake_commit_wt2(*args, **kwargs):
        return None

    monkeypatch.setattr("jig.worktree.commit_worktree", fake_commit_wt2)

    async def fake_try_resolve(ticket_id, ticket):
        return False

    orch._try_resolve_conflict = fake_try_resolve  # type: ignore[method-assign]

    await orch.startup()
    try:
        tid = await orch.tickets.create(
            Ticket(work_type=WorkType.FEATURE, title="f", created_by="user")
        )
        await orch._handle_schedule(tid)
        running_task = orch._running_tickets.get(tid)
        if running_task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(running_task), timeout=2.0)
            except (asyncio.TimeoutError, Exception):
                pass

        ticket = await orch.tickets.get(tid)
        assert ticket is not None
        assert ticket.status == TicketStatus.MERGE_CONFLICT, (
            f"expected MERGE_CONFLICT, got {ticket.status}"
        )
    finally:
        await orch.shutdown()
