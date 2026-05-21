"""Verifies run_agent posts an agent_run SystemEvent on completion."""

from __future__ import annotations

from pathlib import Path

import pytest

from jig.models import PhaseConfig, RoleConfig, WorkflowConfig
from jig.thread import SystemEvent
from jig.ticket import Ticket, TicketStatus, WorkType
from tests._phase5p_helpers import build_orch, poll_until
from tests._test_ticket import TICKET_AC_PLACEHOLDER


@pytest.mark.asyncio
async def test_agent_run_event_posted(tmp_path: Path, monkeypatch) -> None:
    from claude_agent_sdk.types import (
        AssistantMessage,
        TextBlock,
        ResultMessage,
    )

    async def fake_query(*, prompt, options, transport=None):
        async for _ in prompt:
            break
        yield AssistantMessage(content=[TextBlock(text="working")], model="claude-test")
        yield ResultMessage(
            subtype="success",
            duration_ms=1234,
            duration_api_ms=1000,
            is_error=False,
            num_turns=3,
            session_id="",
            total_cost_usd=0.0,
            usage={},
            result="completed the task",
        )

    from jig import agent as agent_module

    monkeypatch.setattr(agent_module, "query", fake_query)

    workflow = WorkflowConfig(
        name="default",
        phases=[PhaseConfig(name="spec", role="spec")],
    )
    roles = [RoleConfig(role="spec", phase_prompt="spec")]
    orch = build_orch(tmp_path, workflow=workflow, roles=roles, monkeypatch=monkeypatch)

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

        async def done() -> bool:
            t = await orch.tickets.get(tid)
            return t is not None and t.status == TicketStatus.RESOLVED

        assert await poll_until(done, timeout_s=5.0)

        entries = await orch.threads.for_ticket(tid)
        runs = [
            e
            for e in entries
            if isinstance(e, SystemEvent) and e.event_type == "agent_run"
        ]
        assert len(runs) == 1
        assert runs[0].payload["num_turns"] == 3
        assert runs[0].payload["duration_ms"] == 1234
        assert runs[0].payload["role"] == "spec"
        assert "completed the task" in runs[0].payload["result_preview"]
    finally:
        await orch.shutdown()
