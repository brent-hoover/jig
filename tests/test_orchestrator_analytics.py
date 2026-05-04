"""Tests for the v2 analytics wiring on the Orchestrator (Track A4 bones).

Three emit sites cover the bones-scenario lifecycle:
- ``TicketStateChanged`` — fired by the TicketStore status-change callback
- ``AgentSpawned`` / ``AgentCompleted`` — fired around ``_run_agent_with_analytics``

These tests exercise the wiring directly (the helpers and callback) rather
than spinning a full orchestrator + Claude SDK; that path is integration
ground covered by test_orchestrator_full_lifecycle.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from jig.analytics.events import AgentCompleted, AgentSpawned, TicketStateChanged
from jig.orchestrator import Orchestrator
from jig.project import Project, save_project
from jig.ticket import Ticket, TicketStatus, WorkType


def _save_project(tmp_path: Path) -> None:
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


@pytest.mark.asyncio
async def test_analytics_store_initialized_on_startup(tmp_path: Path) -> None:
    _save_project(tmp_path)
    orch = Orchestrator(project_path=tmp_path)
    await orch.startup()
    try:
        assert orch.analytics is not None
        assert orch._analytics_emitter is not None
    finally:
        await orch.shutdown()


@pytest.mark.asyncio
async def test_ticket_state_change_emits_analytics_event(tmp_path: Path) -> None:
    _save_project(tmp_path)
    orch = Orchestrator(project_path=tmp_path)
    await orch.startup()
    try:
        assert orch.tickets is not None
        assert orch.analytics is not None

        tid = await orch.tickets.create(
            Ticket(work_type=WorkType.FEATURE, title="t", created_by="u")
        )
        await orch.tickets.update_status(tid, TicketStatus.IN_PROGRESS)
        await orch.tickets.update_status(tid, TicketStatus.RESOLVED)

        # emit_nowait schedules background tasks; drain so the events
        # actually land before we read.
        await orch._analytics_emitter.drain()

        events = await orch.analytics.by_kind("ticket_state_changed")
        assert len(events) == 2
        first, second = sorted(events, key=lambda e: e.timestamp)
        assert isinstance(first, TicketStateChanged)
        assert (first.from_state, first.to_state) == ("open", "in_progress")
        assert (second.from_state, second.to_state) == (
            "in_progress",
            "resolved",
        )
        assert all(e.ticket_id == tid for e in events)
    finally:
        await orch.shutdown()


@pytest.mark.asyncio
async def test_run_agent_with_analytics_emits_spawn_and_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _save_project(tmp_path)
    orch = Orchestrator(project_path=tmp_path)
    await orch.startup()
    try:
        from jig import orchestrator as orchestrator_module

        class _FakeResult:
            status = "success"
            final_text = "ok"
            total_cost_usd = 0.0234
            tokens_in = 1234
            tokens_out = 567

        async def _fake_run_agent(ctx, emitter=None):
            await asyncio.sleep(0)
            return _FakeResult()

        monkeypatch.setattr(orchestrator_module, "run_agent", _fake_run_agent)

        ticket = Ticket(
            work_type=WorkType.FEATURE,
            title="t",
            created_by="u",
        )

        class _FakeCtx:
            role = "dev"

            def __init__(self, t: Ticket) -> None:
                self.ticket = t

        result = await orch._run_agent_with_analytics(_FakeCtx(ticket))
        assert result.status == "success"

        await orch._analytics_emitter.drain()

        spawned = await orch.analytics.by_kind("agent_spawned")
        completed = await orch.analytics.by_kind("agent_completed")
        assert len(spawned) == 1
        assert len(completed) == 1
        s, c = spawned[0], completed[0]
        assert isinstance(s, AgentSpawned)
        assert isinstance(c, AgentCompleted)
        assert s.role == "dev"
        assert s.ticket_id == ticket.id
        assert s.spawned_by == "orchestrator"
        assert s.model == "default"
        assert c.status == "success"
        assert c.duration_ms >= 0
        # Cost + token fields propagate from RunAgentResult into the
        # AgentCompleted event so the simulator's cost_under_budget
        # assertion has real numbers to gate on.
        assert c.cost_estimate_usd == 0.0234
        assert c.tokens_in == 1234
        assert c.tokens_out == 567
    finally:
        await orch.shutdown()


@pytest.mark.asyncio
async def test_run_agent_status_mapping_needs_info_to_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _save_project(tmp_path)
    orch = Orchestrator(project_path=tmp_path)
    await orch.startup()
    try:
        from jig import orchestrator as orchestrator_module

        class _FakeResult:
            status = "needs_info"
            final_text = ""
            total_cost_usd = None
            tokens_in = None
            tokens_out = None

        async def _fake_run_agent(ctx, emitter=None):
            return _FakeResult()

        monkeypatch.setattr(orchestrator_module, "run_agent", _fake_run_agent)

        ticket = Ticket(
            work_type=WorkType.FEATURE, title="t", created_by="u"
        )

        class _FakeCtx:
            role = "dev"

            def __init__(self, t: Ticket) -> None:
                self.ticket = t

        await orch._run_agent_with_analytics(_FakeCtx(ticket))
        await orch._analytics_emitter.drain()

        completed = await orch.analytics.by_kind("agent_completed")
        assert len(completed) == 1
        assert completed[0].status == "blocked"
    finally:
        await orch.shutdown()


@pytest.mark.asyncio
async def test_run_agent_failure_still_emits_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _save_project(tmp_path)
    orch = Orchestrator(project_path=tmp_path)
    await orch.startup()
    try:
        from jig import orchestrator as orchestrator_module

        async def _boom(ctx, emitter=None):
            raise RuntimeError("agent crashed")

        monkeypatch.setattr(orchestrator_module, "run_agent", _boom)

        class _FakeCtx:
            role = "dev"

            def __init__(self) -> None:
                self.ticket = Ticket(
                    work_type=WorkType.FEATURE, title="t", created_by="u"
                )

        with pytest.raises(RuntimeError, match="agent crashed"):
            await orch._run_agent_with_analytics(_FakeCtx())

        await orch._analytics_emitter.drain()

        spawned = await orch.analytics.by_kind("agent_spawned")
        completed = await orch.analytics.by_kind("agent_completed")
        assert len(spawned) == 1
        assert len(completed) == 1
        # Default status before re-raise is "failed"; the helper records
        # it via the finally block so failure paths still get a complete pair.
        assert completed[0].status == "failed"
    finally:
        await orch.shutdown()
