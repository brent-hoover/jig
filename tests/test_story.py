"""Unit tests for jig.story library."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from jig.store.threads import ThreadStore
from jig.story import StoryEvent, StorySource, _render_system_event, build_story
from jig.thread import Handoff, Note, SystemEvent


def test_story_event_frozen_dataclass_construction() -> None:
    ev = StoryEvent(
        ts=datetime(2026, 4, 23, 10, 0, tzinfo=timezone.utc),
        source=StorySource.thread,
        kind="handoff",
        level="info",
        message="handoff posted",
        ticket_id="tid-1",
        phase="spec",
        role="dev",
        raw={"kind": "handoff"},
    )
    assert ev.source == StorySource.thread
    assert ev.kind == "handoff"

    with pytest.raises(Exception):
        ev.ts = datetime.now()  # frozen


def test_story_source_enum_values() -> None:
    assert StorySource.thread.value == "thread"
    assert StorySource.log.value == "log"


@pytest.mark.asyncio
async def test_build_story_returns_empty_list_for_unknown_ticket(
    tmp_path: Path,
) -> None:
    (tmp_path / ".jig" / "store").mkdir(parents=True)
    threads = ThreadStore(tmp_path)

    events = await build_story(
        "no-such-ticket", project_path=tmp_path, threads=threads
    )
    assert events == []


@pytest.mark.asyncio
async def test_build_story_includes_thread_entries(tmp_path: Path) -> None:
    (tmp_path / ".jig" / "store").mkdir(parents=True)
    threads = ThreadStore(tmp_path / ".jig" / "store" / "comments.jsonl")
    await threads.load()

    await threads.post(
        Note(ticket_id="tid-1", author="dev", text="starting work")
    )
    await threads.post(
        Handoff(
            ticket_id="tid-1", author="dev", phase="spec",
            outputs=["spec.md"], summary="spec draft ready",
        )
    )

    events = await build_story(
        "tid-1", project_path=tmp_path, threads=threads
    )
    assert len(events) == 2
    assert events[0].ts <= events[1].ts
    assert events[0].source == StorySource.thread
    assert events[0].kind == "note"
    assert "starting work" in events[0].message
    assert events[1].kind == "handoff"


@pytest.mark.asyncio
async def test_build_story_includes_matching_log_lines(tmp_path: Path) -> None:
    (tmp_path / ".jig" / "store").mkdir(parents=True)
    (tmp_path / ".jig" / "logs").mkdir(parents=True)
    threads = ThreadStore(tmp_path / ".jig" / "store" / "comments.jsonl")
    await threads.load()

    log_path = tmp_path / ".jig" / "logs" / "jig-20260423-100000.jsonl"
    log_path.write_text(
        '{"ts":"2026-04-23T10:00:00.000","level":"INFO","logger":"jig.x",'
        '"msg":"scheduling","ticket_id":"tid-1","phase":null,"role":null,'
        '"agent_id":null}\n'
        '{"ts":"2026-04-23T10:00:01.000","level":"INFO","logger":"jig.y",'
        '"msg":"other ticket","ticket_id":"tid-2","phase":null,"role":null,'
        '"agent_id":null}\n'
    )

    events = await build_story(
        "tid-1", project_path=tmp_path, threads=threads
    )
    assert len(events) == 1
    assert events[0].source == StorySource.log
    assert events[0].kind == "jig.x"
    assert "scheduling" in events[0].message


@pytest.mark.asyncio
async def test_build_story_sorts_thread_and_log_by_timestamp(
    tmp_path: Path,
) -> None:
    (tmp_path / ".jig" / "store").mkdir(parents=True)
    (tmp_path / ".jig" / "logs").mkdir(parents=True)
    threads = ThreadStore(tmp_path / ".jig" / "store" / "comments.jsonl")
    await threads.load()

    # Write a log line in the past
    log_path = tmp_path / ".jig" / "logs" / "jig-20200101-000000.jsonl"
    log_path.write_text(
        '{"ts":"2020-01-01T00:00:00.000","level":"INFO","logger":"jig.x",'
        '"msg":"ancient","ticket_id":"tid-1","phase":null,"role":null,'
        '"agent_id":null}\n'
    )

    # Post a thread entry now
    await threads.post(
        Note(ticket_id="tid-1", author="dev", text="recent")
    )

    events = await build_story(
        "tid-1", project_path=tmp_path, threads=threads
    )
    assert len(events) == 2
    assert "ancient" in events[0].message
    assert "recent" in events[1].message


# ---- Renderer unit tests for the three new system event types --------------


def test_render_system_event_phase_start() -> None:
    ev = SystemEvent(
        ticket_id="tid-1",
        author="orchestrator",
        event_type="phase_start",
        content="spec",
        payload={"phase": "spec", "role": "spec", "spawn_reason": "phase_primary"},
    )
    kind, message = _render_system_event(ev)
    assert kind == "system_event/phase_start"
    assert "PHASE START spec" in message
    assert "role=spec" in message


def test_render_system_event_phase_end() -> None:
    ev = SystemEvent(
        ticket_id="tid-1",
        author="orchestrator",
        event_type="phase_end",
        content="success",
        payload={
            "phase": "dev",
            "role": "dev",
            "outcome": "success",
            "duration_ms": 12345,
        },
    )
    kind, message = _render_system_event(ev)
    assert kind == "system_event/phase_end"
    assert "PHASE END" in message
    assert "dev" in message
    assert "outcome=success" in message
    assert "duration=12345ms" in message


def test_render_system_event_agent_run() -> None:
    ev = SystemEvent(
        ticket_id="tid-1",
        author="orchestrator",
        event_type="agent_run",
        content="dev ran 7 turns",
        payload={
            "role": "dev",
            "num_turns": 7,
            "duration_ms": 4321,
            "spawn_reason": "phase_primary",
            "result_preview": "done",
        },
    )
    kind, message = _render_system_event(ev)
    assert kind == "system_event/agent_run"
    assert "AGENT RUN" in message
    assert "dev" in message
    assert "turns=7" in message
    assert "duration=4321ms" in message
