"""Verifies phase_start and phase_end SystemEvents land on the thread."""

from __future__ import annotations

from pathlib import Path

import pytest

from jig.models import PhaseConfig, RoleConfig, WorkflowConfig
from jig.thread import SystemEvent
from jig.ticket import Ticket, TicketStatus, WorkType
from tests._phase5p_helpers import build_orch, poll_until


@pytest.mark.asyncio
async def test_phase_start_and_end_events_emitted(
    tmp_path: Path, monkeypatch
) -> None:
    workflow = WorkflowConfig(
        name="default",
        phases=[PhaseConfig(name="spec", role="spec")],
    )
    roles = [RoleConfig(role="spec", phase_prompt="spec")]
    orch = build_orch(
        tmp_path, workflow=workflow, roles=roles, monkeypatch=monkeypatch
    )

    from jig import orchestrator as orch_module
    from jig.agent import RunAgentResult

    async def fake_run_agent(ctx, emitter=None):
        return RunAgentResult(status="success", final_text="ok")

    monkeypatch.setattr(orch_module, "run_agent", fake_run_agent)

    await orch.startup()
    try:
        tid = await orch.tickets.create(
            Ticket(work_type=WorkType.FEATURE, title="f", created_by="user")
        )
        await orch._handle_schedule(tid)

        async def done() -> bool:
            t = await orch.tickets.get(tid)
            return t is not None and t.status == TicketStatus.RESOLVED

        assert await poll_until(done, timeout_s=5.0)

        entries = await orch.threads.for_ticket(tid)
        starts = [
            e for e in entries
            if isinstance(e, SystemEvent) and e.event_type == "phase_start"
        ]
        ends = [
            e for e in entries
            if isinstance(e, SystemEvent) and e.event_type == "phase_end"
        ]
        assert len(starts) == 1
        assert len(ends) == 1
        assert starts[0].content == "spec"  # phase name in content
        assert ends[0].content == "success"
        assert ends[0].payload.get("duration_ms") is not None
        assert ends[0].payload["duration_ms"] >= 0
    finally:
        await orch.shutdown()
