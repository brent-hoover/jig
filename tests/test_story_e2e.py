"""End-to-end: run a ticket through the orchestrator, then assert
jig story produces a coherent narrative.

Approach: monkeypatch the SDK ``query`` function so ``run_agent``
runs end-to-end and emits its ``agent_run`` SystemEvent naturally
(same pattern as ``test_agent_run_event.py`` /
``test_agent_thinking_capture.py``). The orchestrator emits
``phase_start`` / ``phase_end`` around that run, and DEBUG log lines
fall out of the thinking block via ``configure_logging(verbose=True)``.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from tests._test_ticket import TICKET_AC_PLACEHOLDER


@pytest.mark.asyncio
async def test_story_captures_orchestrator_agent_and_thread_events(
    tmp_path: Path, monkeypatch
) -> None:
    from claude_agent_sdk.types import (
        AssistantMessage,
        ResultMessage,
        TextBlock,
        ThinkingBlock,
    )

    from jig.logging_setup import configure_logging
    from jig.models import PhaseConfig, RoleConfig, WorkflowConfig
    from jig.store.threads import ThreadStore
    from jig.store.tickets import TicketStore
    from jig.story import StorySource, build_story
    from jig.thread import Note
    from jig.ticket import Ticket, TicketStatus, WorkType
    from tests._phase5p_helpers import build_orch, poll_until

    (tmp_path / ".jig").mkdir(exist_ok=True)
    configure_logging(tmp_path, verbose=True)

    async def fake_query(*, prompt, options, transport=None):
        async for _ in prompt:
            break
        yield AssistantMessage(
            content=[
                ThinkingBlock(
                    thinking="Plan: read the brief, write a note.",
                    signature="sig",
                ),
                TextBlock(text="Working on it."),
            ],
            model="claude-test",
        )
        yield ResultMessage(
            subtype="success",
            duration_ms=200,
            duration_api_ms=180,
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
        # Pre-post a Note so thread content shows up alongside
        # orchestrator-emitted events.
        await orch.threads.post(
            Note(ticket_id=tid, author="user", text="please do the thing")
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

    # ThreadStore / TicketStore take FILE paths, not directories.
    threads = ThreadStore(tmp_path / ".jig" / "store" / "comments.jsonl")
    tickets = TicketStore(tmp_path / ".jig" / "store" / "tickets.jsonl")
    await threads.load()
    await tickets.load()
    story = await build_story(
        tid,
        project_path=tmp_path,
        threads=threads,
        tickets=tickets,
    )

    # The story must contain: the user note, phase_start, agent_run,
    # phase_end, and at least one log-sourced event.
    kinds = [e.kind for e in story]
    assert "note" in kinds
    assert "system_event/phase_start" in kinds
    assert "system_event/agent_run" in kinds
    assert "system_event/phase_end" in kinds
    # Log lines from the orchestrator or agent
    log_events = [e for e in story if e.source == StorySource.log]
    assert log_events, "expected at least one log event on the story"

    # Timestamp ordering: phase_start before phase_end.
    start_idx = kinds.index("system_event/phase_start")
    end_idx = kinds.index("system_event/phase_end")
    assert start_idx < end_idx
