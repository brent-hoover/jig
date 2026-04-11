from pathlib import Path

import pytest

from jig.store.tickets import TicketStore
from jig.ticket import Ticket, TicketStatus, TicketType


@pytest.mark.asyncio
async def test_create_and_get(tmp_path: Path) -> None:
    store = TicketStore(tmp_path / "tickets.jsonl")
    await store.load()
    t = Ticket(type=TicketType.FEATURE, title="f1", created_by="user")
    tid = await store.create(t)
    loaded = await store.get(tid)
    assert loaded is not None
    assert loaded.title == "f1"
    assert loaded.id == tid


@pytest.mark.asyncio
async def test_find_in_progress_top_level(tmp_path: Path) -> None:
    store = TicketStore(tmp_path / "tickets.jsonl")
    await store.load()
    f = Ticket(type=TicketType.FEATURE, title="f", created_by="u", status=TicketStatus.IN_PROGRESS)
    b = Ticket(type=TicketType.BUG, title="b", created_by="u", status=TicketStatus.IN_PROGRESS)
    c = Ticket(type=TicketType.CHORE, title="c", created_by="u", status=TicketStatus.OPEN)
    task = Ticket(type=TicketType.TASK, title="t", created_by="o", status=TicketStatus.IN_PROGRESS)
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
    await store.create(Ticket(type=TicketType.TASK, title="a", created_by="o", assignee="dev"))
    await store.create(Ticket(type=TicketType.TASK, title="b", created_by="o", assignee="qa"))
    await store.create(Ticket(type=TicketType.TASK, title="c", created_by="o", assignee="dev"))

    dev_tickets = await store.find_by_assignee("dev")
    assert sorted(t.title for t in dev_tickets) == ["a", "c"]


@pytest.mark.asyncio
async def test_update_status_bumps_updated_at(tmp_path: Path) -> None:
    store = TicketStore(tmp_path / "tickets.jsonl")
    await store.load()
    tid = await store.create(Ticket(type=TicketType.FEATURE, title="f", created_by="u"))
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
    tid = await store.create(Ticket(type=TicketType.BUG, title="b", created_by="u"))
    await store.update_status(tid, TicketStatus.RESOLVED)

    store2 = TicketStore(path)
    await store2.load()
    loaded = await store2.get(tid)
    assert loaded.status == TicketStatus.RESOLVED
