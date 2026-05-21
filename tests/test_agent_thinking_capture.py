"""Verifies run_agent captures ThinkingBlock into logs at DEBUG."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from jig.logging_setup import configure_logging
from tests._test_ticket import TICKET_AC_PLACEHOLDER


@pytest.mark.asyncio
async def test_thinking_block_is_captured_at_debug(tmp_path: Path, monkeypatch) -> None:
    from jig.models import PhaseConfig, RoleConfig, WorkflowConfig
    from jig.ticket import Ticket, TicketStatus, WorkType
    from tests._phase5p_helpers import build_orch, poll_until

    (tmp_path / ".jig").mkdir(exist_ok=True)
    log_file = configure_logging(tmp_path, verbose=True)

    # Stub the SDK's `query` function so it emits a synthetic
    # AssistantMessage containing a ThinkingBlock, then a ResultMessage.
    from claude_agent_sdk.types import (
        AssistantMessage,
        ResultMessage,
        TextBlock,
        ThinkingBlock,
    )

    async def fake_query(*, prompt, options, transport=None):
        # Drain the prompt stream once to simulate initial turn
        async for _ in prompt:
            break
        yield AssistantMessage(
            content=[
                ThinkingBlock(
                    thinking="I should read the spec first, then plan the changes.",
                    signature="sig-abc",
                ),
                TextBlock(text="Starting work."),
            ],
            model="claude-test",
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
    finally:
        await orch.shutdown()
        for h in logging.getLogger().handlers:
            h.flush()

    lines = log_file.read_text().strip().splitlines()
    recs = [json.loads(line) for line in lines]
    thinking_recs = [
        r for r in recs if r.get("level") == "DEBUG" and "thinking:" in r.get("msg", "")
    ]
    assert thinking_recs, "expected at least one DEBUG thinking: log line"
    assert "read the spec first" in thinking_recs[0]["msg"]
    assert thinking_recs[0]["ticket_id"] == tid


@pytest.mark.asyncio
async def test_large_thinking_block_is_truncated(tmp_path: Path, monkeypatch) -> None:
    """Thinking payloads over _LOG_TRUNCATE_BYTES are truncated and
    produce a companion ``thinking_truncated`` DEBUG line recording
    the original byte length.
    """
    from jig.agent import _LOG_TRUNCATE_BYTES
    from jig.models import PhaseConfig, RoleConfig, WorkflowConfig
    from jig.ticket import Ticket, TicketStatus, WorkType
    from tests._phase5p_helpers import build_orch, poll_until

    (tmp_path / ".jig").mkdir(exist_ok=True)
    log_file = configure_logging(tmp_path, verbose=True)

    from claude_agent_sdk.types import (
        AssistantMessage,
        ResultMessage,
        ThinkingBlock,
    )

    # Payload larger than the truncation threshold. ASCII so bytes == chars.
    big_thinking = "A" * (_LOG_TRUNCATE_BYTES + 5000)

    async def fake_query(*, prompt, options, transport=None):
        async for _ in prompt:
            break
        yield AssistantMessage(
            content=[
                ThinkingBlock(thinking=big_thinking, signature="sig-big"),
            ],
            model="claude-test",
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
    finally:
        await orch.shutdown()
        for h in logging.getLogger().handlers:
            h.flush()

    lines = log_file.read_text().strip().splitlines()
    recs = [json.loads(line) for line in lines]

    # Companion line: records the true byte length.
    truncated_markers = [
        r
        for r in recs
        if r.get("level") == "DEBUG" and "thinking_truncated:" in r.get("msg", "")
    ]
    assert truncated_markers, "expected a thinking_truncated DEBUG line"
    expected_bytes = len(big_thinking.encode("utf-8"))
    assert f"full_bytes={expected_bytes}" in truncated_markers[0]["msg"]

    # Main thinking: line still emitted, but shorter than the raw payload.
    thinking_lines = [
        r
        for r in recs
        if r.get("level") == "DEBUG"
        and r.get("msg", "").startswith("[")
        and "] thinking: " in r.get("msg", "")
    ]
    assert thinking_lines, "expected a thinking: DEBUG line"
    body = thinking_lines[0]["msg"].split("] thinking: ", 1)[1]
    assert len(body.encode("utf-8")) <= _LOG_TRUNCATE_BYTES
    assert len(body) < len(big_thinking)
