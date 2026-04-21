import json
from pathlib import Path

import pytest

from jig.store.tickets import TicketStore
from jig.ticket import Size, Ticket, TicketStatus, WorkType


@pytest.mark.asyncio
async def test_create_and_get(tmp_path: Path) -> None:
    store = TicketStore(tmp_path / "tickets.jsonl")
    await store.load()
    t = Ticket(work_type=WorkType.FEATURE, title="f1", created_by="user")
    tid = await store.create(t)
    loaded = await store.get(tid)
    assert loaded is not None
    assert loaded.title == "f1"
    assert loaded.id == tid


@pytest.mark.asyncio
async def test_find_in_progress_top_level(tmp_path: Path) -> None:
    store = TicketStore(tmp_path / "tickets.jsonl")
    await store.load()
    f = Ticket(work_type=WorkType.FEATURE, title="f", created_by="u", status=TicketStatus.IN_PROGRESS)
    b = Ticket(work_type=WorkType.BUGFIX, title="b", created_by="u", status=TicketStatus.IN_PROGRESS)
    c = Ticket(work_type=WorkType.REFACTOR, title="c", created_by="u", status=TicketStatus.OPEN)
    # Thread-style tickets are excluded from workflow-top-level queries.
    task = Ticket(
        work_type=WorkType.REFACTOR, title="t", created_by="o",
        status=TicketStatus.IN_PROGRESS, workflow="thread",
    )
    await store.create(f)
    await store.create(b)
    await store.create(c)
    await store.create(task)

    found = await store.find_in_progress_top_level()
    titles = sorted(t.title for t in found)
    assert titles == ["b", "f"]


@pytest.mark.asyncio
async def test_find_by_assignee_uses_index(tmp_path: Path) -> None:
    store = TicketStore(tmp_path / "tickets.jsonl")
    await store.load()
    await store.create(Ticket(work_type=WorkType.REFACTOR, title="a", created_by="o", assignee="dev"))
    await store.create(Ticket(work_type=WorkType.REFACTOR, title="b", created_by="o", assignee="qa"))
    await store.create(Ticket(work_type=WorkType.REFACTOR, title="c", created_by="o", assignee="dev"))

    dev_tickets = await store.find_by_assignee("dev")
    assert sorted(t.title for t in dev_tickets) == ["a", "c"]


@pytest.mark.asyncio
async def test_update_status_bumps_updated_at(tmp_path: Path) -> None:
    store = TicketStore(tmp_path / "tickets.jsonl")
    await store.load()
    tid = await store.create(Ticket(work_type=WorkType.FEATURE, title="f", created_by="u"))
    before = (await store.get(tid)).updated_at
    await store.update_status(tid, TicketStatus.IN_PROGRESS)
    after = await store.get(tid)
    assert after.status == TicketStatus.IN_PROGRESS
    assert after.updated_at >= before


@pytest.mark.asyncio
async def test_reload_replays_log(tmp_path: Path) -> None:
    path = tmp_path / "tickets.jsonl"
    store = TicketStore(path)
    await store.load()
    tid = await store.create(Ticket(work_type=WorkType.BUGFIX, title="b", created_by="u"))
    await store.update_status(tid, TicketStatus.RESOLVED)

    store2 = TicketStore(path)
    await store2.load()
    loaded = await store2.get(tid)
    assert loaded.status == TicketStatus.RESOLVED


@pytest.mark.asyncio
async def test_work_type_and_size_round_trip(tmp_path: Path) -> None:
    """New classification fields must survive a save/reload cycle."""
    path = tmp_path / "tickets.jsonl"
    store = TicketStore(path)
    await store.load()
    tid = await store.create(
        Ticket(
            work_type=WorkType.MIGRATION,
            size=Size.XL,
            title="cut over",
            created_by="u",
        )
    )

    store2 = TicketStore(path)
    await store2.load()
    loaded = await store2.get(tid)
    assert loaded is not None
    assert loaded.work_type == WorkType.MIGRATION
    assert loaded.size == Size.XL


@pytest.mark.asyncio
async def test_legacy_jsonl_records_load(tmp_path: Path) -> None:
    """Hand-written pre-doc-03 JSONL must still deserialize cleanly.

    Covers the migration path for any local dev JSONL that predates the
    `type` → `work_type` rename. The store replays each line through
    Ticket(...), so the model's `_migrate_legacy_fields` validator is
    what makes this work.
    """
    path = tmp_path / "tickets.jsonl"
    path.write_text(
        json.dumps(
            {
                "_op": "insert",
                "_id": "T-legacy",
                "type": "bug",
                "title": "old",
                "status": "open",
                "created_by": "u",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
            }
        )
        + "\n"
    )

    store = TicketStore(path)
    await store.load()
    loaded = await store.get("T-legacy")
    assert loaded is not None
    assert loaded.work_type == WorkType.BUGFIX
    assert loaded.size == Size.M


@pytest.mark.asyncio
async def test_update_accepts_raw_datetime(tmp_path: Path) -> None:
    """Regression test for TypedCollection.update serialization.

    Previously, raw datetime values passed into update() would crash at
    json.dumps in the underlying JsonlStore. This test confirms that
    TicketStore.update (and by extension TypedCollection.update) now
    serializes non-JSON-primitive values via pydantic before writing.
    """
    from datetime import datetime, timezone

    store = TicketStore(tmp_path / "tickets.jsonl")
    await store.load()
    tid = await store.create(Ticket(work_type=WorkType.FEATURE, title="t", created_by="u"))
    # Pass a raw datetime — must not raise.
    when = datetime(2030, 1, 1, tzinfo=timezone.utc)
    updated = await store.update(tid, updated_at=when)
    assert updated.updated_at == when
