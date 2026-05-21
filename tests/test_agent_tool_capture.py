"""Verifies full tool inputs and tool results are captured at DEBUG."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from jig.logging_setup import configure_logging
from tests._test_ticket import TICKET_AC_PLACEHOLDER


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
                    content=("total 4\ndrwxr-xr-x 2 root root 4096 Apr 23 10:00 ."),
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
    finally:
        await orch.shutdown()
        for h in logging.getLogger().handlers:
            h.flush()

    recs = [json.loads(line) for line in log_file.read_text().strip().splitlines()]
    input_recs = [r for r in recs if "tool_input:" in r.get("msg", "")]
    result_recs = [r for r in recs if "tool_result:" in r.get("msg", "")]
    assert input_recs, "no tool_input DEBUG line"
    assert "ls -la /tmp/foo" in input_recs[0]["msg"]
    assert result_recs, "no tool_result DEBUG line"
    assert "tu-1" in result_recs[0]["msg"]
    assert "total 4" in result_recs[0]["msg"]


@pytest.mark.asyncio
async def test_large_tool_result_is_truncated_and_list_content_serialised(
    tmp_path: Path, monkeypatch
) -> None:
    """Two tool-result edge cases exercised in one run:

    1. A >32KB string content produces a ``tool_result_truncated`` DEBUG
       line with the true byte length, and the main ``tool_result``
       line carries a shortened body.
    2. A list-of-dicts content (which the SDK sometimes returns) is
       serialised via ``json.dumps`` before being written to the log.
    """
    from claude_agent_sdk.types import (
        AssistantMessage,
        ResultMessage,
        ToolResultBlock,
        ToolUseBlock,
        UserMessage,
    )

    from jig.agent import _LOG_TRUNCATE_BYTES
    from jig.models import PhaseConfig, RoleConfig, WorkflowConfig
    from jig.ticket import Ticket, TicketStatus, WorkType
    from tests._phase5p_helpers import build_orch, poll_until

    (tmp_path / ".jig").mkdir(exist_ok=True)
    log_file = configure_logging(tmp_path, verbose=True)

    big_body = "Z" * (_LOG_TRUNCATE_BYTES + 5000)
    list_content = [
        {"type": "text", "text": "structured-marker-payload"},
        {"type": "image", "url": "https://example.invalid/x.png"},
    ]

    async def fake_query(*, prompt, options, transport=None):
        async for _ in prompt:
            break
        yield AssistantMessage(
            content=[
                ToolUseBlock(id="tu-big", name="Bash", input={"command": "x"}),
                ToolUseBlock(id="tu-list", name="Read", input={"file_path": "x"}),
            ],
            model="claude-test",
        )
        yield UserMessage(
            content=[
                ToolResultBlock(tool_use_id="tu-big", content=big_body, is_error=True),
                ToolResultBlock(
                    tool_use_id="tu-list", content=list_content, is_error=False
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
        name="default", phases=[PhaseConfig(name="dev", role="dev")]
    )
    roles = [RoleConfig(role="dev", phase_prompt="dev")]
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
    finally:
        await orch.shutdown()
        for h in logging.getLogger().handlers:
            h.flush()

    recs = [json.loads(line) for line in log_file.read_text().strip().splitlines()]

    # (1) Truncation: companion marker + shortened body + is_error=True.
    trunc_markers = [
        r
        for r in recs
        if "tool_result_truncated:" in r.get("msg", "")
        and "id=tu-big" in r.get("msg", "")
    ]
    assert trunc_markers, "expected tool_result_truncated line for tu-big"
    expected_bytes = len(big_body.encode("utf-8"))
    assert f"full_bytes={expected_bytes}" in trunc_markers[0]["msg"]

    big_results = [
        r
        for r in recs
        if "tool_result:" in r.get("msg", "") and "id=tu-big" in r.get("msg", "")
    ]
    assert big_results
    big_msg = big_results[0]["msg"]
    assert "is_error=True" in big_msg
    body = big_msg.split("] tool_result: ", 1)[1]
    # body still contains the id=... is_error=... prefix; strip.
    body_payload = body.split(" ", 2)[2]
    assert len(body_payload.encode("utf-8")) <= _LOG_TRUNCATE_BYTES
    assert len(body_payload) < len(big_body)

    # (2) List content: serialised to JSON.
    list_results = [
        r
        for r in recs
        if "tool_result:" in r.get("msg", "") and "id=tu-list" in r.get("msg", "")
    ]
    assert list_results
    list_msg = list_results[0]["msg"]
    assert "is_error=False" in list_msg
    assert "structured-marker-payload" in list_msg
    # JSON-array dumps of our list start with `[{` — confirm we went
    # through json.dumps and didn't just cast the list to str().
    assert "[{" in list_msg
