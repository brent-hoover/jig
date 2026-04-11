from pathlib import Path

import pytest

from jig.store import MessageBus
from jig.store.comments import CommentStore
from jig.store.tickets import TicketStore
from jig.ticket import TicketStatus, TicketType
from jig.ticket_mcp import (
    handle_create_ticket,
    handle_read_ticket,
    handle_list_tickets,
    handle_read_comments,
)


@pytest.fixture
async def stores(tmp_path: Path):
    tickets = TicketStore(tmp_path / "tickets.jsonl")
    comments = CommentStore(tmp_path / "comments.jsonl")
    bus = MessageBus(tmp_path / "messages.jsonl")
    await tickets.load()
    await comments.load()
    await bus.load()
    return tickets, comments, bus


@pytest.mark.asyncio
async def test_create_ticket_persists(stores) -> None:
    tickets, comments, bus = stores
    ticket_id = await handle_create_ticket(
        tickets=tickets,
        comments=comments,
        bus=bus,
        sender="user",
        args={
            "type": "feature",
            "title": "Add search",
            "description": "users want to search",
        },
    )
    loaded = await tickets.get(ticket_id)
    assert loaded is not None
    assert loaded.title == "Add search"
    assert loaded.type == TicketType.FEATURE
    assert loaded.created_by == "user"
    assert loaded.status == TicketStatus.OPEN


@pytest.mark.asyncio
async def test_create_ticket_publishes_bus_event(stores) -> None:
    tickets, comments, bus = stores
    queue = await bus.subscribe("orchestrator")
    await handle_create_ticket(
        tickets=tickets,
        comments=comments,
        bus=bus,
        sender="user",
        args={"type": "bug", "title": "crash"},
    )
    msg = await queue.get()
    assert msg.topic == "orchestrator"
    assert msg.payload["kind"] == "ticket_created"
    assert msg.payload["type"] == "bug"


@pytest.mark.asyncio
async def test_read_ticket(stores) -> None:
    tickets, comments, bus = stores
    tid = await handle_create_ticket(
        tickets=tickets, comments=comments, bus=bus, sender="user",
        args={"type": "feature", "title": "f"},
    )
    loaded = await handle_read_ticket(tickets=tickets, ticket_id=tid)
    assert loaded.title == "f"


@pytest.mark.asyncio
async def test_read_ticket_missing_raises(stores) -> None:
    tickets, _, _ = stores
    with pytest.raises(KeyError):
        await handle_read_ticket(tickets=tickets, ticket_id="nope")


@pytest.mark.asyncio
async def test_list_tickets_filtered(stores) -> None:
    tickets, comments, bus = stores
    await handle_create_ticket(
        tickets=tickets, comments=comments, bus=bus, sender="u",
        args={"type": "feature", "title": "f1"},
    )
    await handle_create_ticket(
        tickets=tickets, comments=comments, bus=bus, sender="u",
        args={"type": "bug", "title": "b1"},
    )
    features = await handle_list_tickets(tickets=tickets, args={"type": "feature"})
    assert [t.title for t in features] == ["f1"]


@pytest.mark.asyncio
async def test_read_comments_direct_post(stores) -> None:
    tickets, comments, bus = stores
    tid = await handle_create_ticket(
        tickets=tickets, comments=comments, bus=bus, sender="u",
        args={"type": "task", "title": "t"},
    )
    from jig.ticket import Comment
    await comments.post(
        Comment(ticket_id=tid, author="dev", content="hello")
    )
    all_c = await handle_read_comments(comments=comments, ticket_id=tid)
    assert [c.content for c in all_c] == ["hello"]
