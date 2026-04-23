"""Verifies MCP tool handlers inherit correlation fields at call time."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from jig.logging_setup import (
    _agent_id_var,
    _phase_var,
    _role_var,
    _ticket_id_var,
    configure_logging,
)
from jig.mcp_server import _wrap_with_context


@pytest.mark.asyncio
async def test_wrap_with_context_sets_and_resets_contextvars() -> None:
    captured: dict = {}

    async def handler(args):
        captured["ticket_id"] = _ticket_id_var.get()
        captured["phase"] = _phase_var.get()
        captured["role"] = _role_var.get()
        captured["agent_id"] = _agent_id_var.get()
        return {"ok": True}

    wrapped = _wrap_with_context(
        handler,
        ticket_id="tid-abc",
        phase="spec",
        role="dev",
        agent_id="dev:tid-abc",
    )

    # Call from a context where nothing is set.
    result = await wrapped({"arg": 1})
    assert result == {"ok": True}

    assert captured == {
        "ticket_id": "tid-abc",
        "phase": "spec",
        "role": "dev",
        "agent_id": "dev:tid-abc",
    }
    # After the call, all four vars should be unset again.
    assert _ticket_id_var.get() is None
    assert _phase_var.get() is None
    assert _role_var.get() is None
    assert _agent_id_var.get() is None


@pytest.mark.asyncio
async def test_mcp_tool_call_logs_carry_correlation(
    tmp_path: Path, monkeypatch
) -> None:
    from jig.models import PhaseConfig, RoleConfig, WorkflowConfig
    from jig.ticket import Ticket, TicketStatus, WorkType
    from tests._phase5p_helpers import build_orch, poll_until

    (tmp_path / ".jig").mkdir(exist_ok=True)
    log_file = configure_logging(tmp_path, verbose=True)

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
    from jig.thread import Note

    async def fake_run_agent(ctx, emitter=None):
        await ctx.threads.post(
            Note(ticket_id=ctx.ticket.id, author="spec", text="hi")
        )
        logging.getLogger("jig.test.during_tool").info("mid-tool")
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
    finally:
        await orch.shutdown()
        for h in logging.getLogger().handlers:
            h.flush()

    lines = log_file.read_text().strip().splitlines()
    matches = [
        json.loads(line)
        for line in lines
        if json.loads(line).get("logger") == "jig.test.during_tool"
    ]
    assert matches
    rec = matches[0]
    assert rec["ticket_id"] == tid
    assert rec["role"] == "spec"
