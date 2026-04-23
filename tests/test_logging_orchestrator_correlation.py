"""Verifies the orchestrator stamps ticket_id / phase / role on log
records emitted from inside a per-ticket path."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from jig.models import PhaseConfig, RoleConfig, WorkflowConfig
from jig.ticket import Ticket, TicketStatus, WorkType
from tests._phase5p_helpers import build_orch, poll_until


@pytest.mark.asyncio
async def test_orchestrator_log_records_carry_ticket_and_phase(
    tmp_path: Path, monkeypatch
) -> None:
    # Wire logging through the real setup so the JSONL file is
    # produced.
    from jig.logging_setup import configure_logging

    (tmp_path / ".jig").mkdir()
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

    async def fake_run_agent(ctx, emitter=None):
        # Emit a log line from within the per-ticket path.
        logging.getLogger("jig.test.agent").info("hello from agent")
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

    # Scan the JSONL file for a record from our test logger.
    lines = log_file.read_text().strip().splitlines()
    matches = [
        json.loads(line)
        for line in lines
        if json.loads(line).get("logger") == "jig.test.agent"
    ]
    assert matches, f"no log records from jig.test.agent; wrote {len(lines)} lines"
    rec = matches[0]
    assert rec["ticket_id"] == tid
    assert rec["phase"] == "spec"
    assert rec["role"] == "spec"
