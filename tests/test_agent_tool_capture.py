"""Verifies full tool inputs and tool results are captured at DEBUG."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from jig.logging_setup import configure_logging


@pytest.mark.asyncio
async def test_tool_input_and_result_captured_at_debug(
    tmp_path: Path, monkeypatch
) -> None:
    from claude_agent_sdk.types import (
        AssistantMessage,
        ResultMessage,
        ToolResultBlock,
        ToolUseBlock,
        UserMessage,
    )

    from jig.models import PhaseConfig, RoleConfig, WorkflowConfig
    from jig.ticket import Ticket, TicketStatus, WorkType
    from tests._phase5p_helpers import build_orch, poll_until

    (tmp_path / ".jig").mkdir(exist_ok=True)
    log_file = configure_logging(tmp_path, verbose=True)

    async def fake_query(*, prompt, options, transport=None):
        async for _ in prompt:
            break
        yield AssistantMessage(
            content=[
                ToolUseBlock(
                    id="tu-1",
                    name="Bash",
                    input={"command": "ls -la /tmp/foo"},
                ),
            ],
            model="claude-test",
        )
        yield UserMessage(
            content=[
                ToolResultBlock(
                    tool_use_id="tu-1",
                    content=(
                        "total 4\n"
                        "drwxr-xr-x 2 root root 4096 Apr 23 10:00 ."
                    ),
                    is_error=False,
                ),
            ],
        )
        yield ResultMessage(
            subtype="success",
            duration_ms=50,
            duration_api_ms=40,
            is_error=False,
            num_turns=1,
            session_id="",
            total_cost_usd=0.0,
            usage={},
            result="done",
        )

    from jig import agent as agent_module

    monkeypatch.setattr(agent_module, "query", fake_query)

    workflow = WorkflowConfig(
        name="default",
        phases=[PhaseConfig(name="dev", role="dev")],
    )
    roles = [RoleConfig(role="dev", phase_prompt="dev")]
    orch = build_orch(
        tmp_path, workflow=workflow, roles=roles, monkeypatch=monkeypatch
    )

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

    recs = [
        json.loads(line) for line in log_file.read_text().strip().splitlines()
    ]
    input_recs = [r for r in recs if "tool_input:" in r.get("msg", "")]
    result_recs = [r for r in recs if "tool_result:" in r.get("msg", "")]
    assert input_recs, "no tool_input DEBUG line"
    assert "ls -la /tmp/foo" in input_recs[0]["msg"]
    assert result_recs, "no tool_result DEBUG line"
    assert "tu-1" in result_recs[0]["msg"]
    assert "total 4" in result_recs[0]["msg"]
