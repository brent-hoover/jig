"""Unit tests for jig.story library."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from jig.story import StoryEvent, StorySource, build_story


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
    from jig.store.threads import ThreadStore

    (tmp_path / ".jig" / "store").mkdir(parents=True)
    threads = ThreadStore(tmp_path)

    events = await build_story(
        "no-such-ticket", project_path=tmp_path, threads=threads
    )
    assert events == []
