from pathlib import Path

import pytest

from jig.models import AgentTypeConfig
from jig.store import MessageBus
from jig.store.comments import CommentStore
from jig.store.tickets import TicketStore
from jig.ticket import Comment, TicketStatus, TicketType
from jig.ticket_mcp import (
    handle_comment_on_ticket,
    handle_create_ticket,
    handle_list_tickets,
    handle_read_comments,
    handle_read_ticket,
    handle_update_ticket,
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
    await comments.post(
        Comment(ticket_id=tid, author="dev", content="hello")
    )
    all_c = await handle_read_comments(comments=comments, ticket_id=tid)
    assert [c.content for c in all_c] == ["hello"]


@pytest.mark.asyncio
async def test_comment_on_ticket_rejects_system_kinds(stores) -> None:
    tickets, comments, bus = stores
    tid = await handle_create_ticket(
        tickets=tickets, comments=comments, bus=bus, sender="u",
        args={"type": "task", "title": "t"},
    )
    with pytest.raises(ValueError):
        await handle_comment_on_ticket(
            tickets=tickets, comments=comments, bus=bus,
            sender="dev", sender_cfg=None,
            args={"ticket_id": tid, "content": "x", "kind": "phase_run"},
        )


@pytest.mark.asyncio
async def test_update_ticket_status_emits_status_change_comment(stores) -> None:
    tickets, comments, bus = stores
    tid = await handle_create_ticket(
        tickets=tickets, comments=comments, bus=bus, sender="u",
        args={"type": "feature", "title": "f"},
    )
    await handle_update_ticket(
        tickets=tickets, comments=comments, bus=bus,
        sender="orchestrator",
        args={"ticket_id": tid, "status": "in_progress"},
    )
    status_changes = [
        c for c in await comments.for_ticket(tid) if c.kind == "status_change"
    ]
    assert len(status_changes) == 1
    assert "in_progress" in status_changes[0].content


@pytest.mark.asyncio
async def test_update_ticket_non_status_field(stores) -> None:
    tickets, comments, bus = stores
    tid = await handle_create_ticket(
        tickets=tickets, comments=comments, bus=bus, sender="u",
        args={"type": "feature", "title": "f"},
    )
    await handle_update_ticket(
        tickets=tickets, comments=comments, bus=bus,
        sender="orchestrator",
        args={"ticket_id": tid, "description": "more detail"},
    )
    loaded = await tickets.get(tid)
    assert loaded.description == "more detail"
    assert not any(c.kind == "status_change" for c in await comments.for_ticket(tid))


@pytest.mark.asyncio
async def test_comment_on_ticket_self_role_allowed(stores) -> None:
    """A dev agent can comment on a dev-assigned ticket even with empty can_message."""
    tickets, comments, bus = stores
    tid = await handle_create_ticket(
        tickets=tickets, comments=comments, bus=bus, sender="u",
        args={"type": "task", "title": "t", "assignee": "dev"},
    )
    dev_cfg = AgentTypeConfig(role="dev", phase_prompt="", can_message=[])
    cid = await handle_comment_on_ticket(
        tickets=tickets, comments=comments, bus=bus,
        sender="dev", sender_cfg=dev_cfg,
        args={"ticket_id": tid, "content": "progress"},
    )
    assert cid


@pytest.mark.asyncio
async def test_comment_on_ticket_orchestrator_always_reachable(stores) -> None:
    """Any agent with empty can_message can still reach the orchestrator."""
    tickets, comments, bus = stores
    tid = await handle_create_ticket(
        tickets=tickets, comments=comments, bus=bus, sender="u",
        args={"type": "task", "title": "t", "assignee": "orchestrator"},
    )
    dev_cfg = AgentTypeConfig(role="dev", phase_prompt="", can_message=[])
    cid = await handle_comment_on_ticket(
        tickets=tickets, comments=comments, bus=bus,
        sender="dev", sender_cfg=dev_cfg,
        args={"ticket_id": tid, "content": "question for orchestrator"},
    )
    assert cid


@pytest.mark.asyncio
async def test_commit_progress_creates_commit_and_comment(stores, tmp_path) -> None:
    import subprocess
    tickets, comments, bus = stores

    work = tmp_path / "worktree"
    work.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=work, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=work, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=work, check=True)
    (work / "a.txt").write_text("hello")

    tid = await handle_create_ticket(
        tickets=tickets, comments=comments, bus=bus, sender="u",
        args={"type": "feature", "title": "f"},
    )
    from jig.ticket_mcp import handle_commit_progress
    result = await handle_commit_progress(
        tickets=tickets, comments=comments, bus=bus,
        sender="dev",
        worktree_path=work,
        args={"ticket_id": tid, "message": "add a.txt"},
    )
    assert "sha" in result
    assert result["sha"]
    commit_comments = await comments.commits_for(tid)
    assert len(commit_comments) == 1
    assert commit_comments[0].commit_sha == result["sha"]
    assert commit_comments[0].content == "feat(dev): add a.txt"


@pytest.mark.asyncio
async def test_commit_progress_nothing_to_commit_returns_none_sha(stores, tmp_path) -> None:
    import subprocess
    tickets, comments, bus = stores
    work = tmp_path / "worktree2"
    work.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=work, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=work, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=work, check=True)
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "init"], cwd=work, check=True)

    tid = await handle_create_ticket(
        tickets=tickets, comments=comments, bus=bus, sender="u",
        args={"type": "feature", "title": "f"},
    )
    from jig.ticket_mcp import handle_commit_progress
    result = await handle_commit_progress(
        tickets=tickets, comments=comments, bus=bus,
        sender="dev", worktree_path=work,
        args={"ticket_id": tid, "message": "noop"},
    )
    assert result["sha"] is None
    assert await comments.commits_for(tid) == []


@pytest.mark.asyncio
async def test_record_learning_writes_to_memory_store(tmp_path: Path) -> None:
    from jig.store.memory import MemoryStore
    from jig.ticket_mcp import handle_record_learning

    memory = MemoryStore(tmp_path)
    await memory.load()
    await handle_record_learning(
        memory=memory, role="dev",
        args={"content": "always use uv run"},
    )
    learnings = await memory.get_role_learnings("dev")
    assert [learning.content for learning in learnings] == ["always use uv run"]


@pytest.mark.asyncio
async def test_request_context_reads_worktree_file(tmp_path: Path) -> None:
    from jig.ticket_mcp import handle_request_context

    work = tmp_path / "w"
    work.mkdir()
    (work / "README.md").write_text("hello")
    result = await handle_request_context(
        worktree_path=work, args={"path": "README.md"},
    )
    assert result == "hello"


@pytest.mark.asyncio
async def test_request_context_missing_file(tmp_path: Path) -> None:
    from jig.ticket_mcp import handle_request_context

    work = tmp_path / "w"
    work.mkdir()
    result = await handle_request_context(
        worktree_path=work, args={"path": "nope.txt"},
    )
    assert "not found" in result.lower()
