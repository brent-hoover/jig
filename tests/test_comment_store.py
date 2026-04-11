import pytest
from pathlib import Path
from jig.store.comments import CommentStore
from jig.ticket import Comment


@pytest.mark.asyncio
async def test_post_and_read(tmp_path: Path) -> None:
    store = CommentStore(tmp_path / "comments.jsonl")
    await store.load()
    cid = await store.post(
        Comment(ticket_id="T1", author="dev-1", content="hello")
    )
    assert cid
    comments = await store.for_ticket("T1")
    assert len(comments) == 1
    assert comments[0].content == "hello"


@pytest.mark.asyncio
async def test_phase_runs_for_ticket(tmp_path: Path) -> None:
    store = CommentStore(tmp_path / "comments.jsonl")
    await store.load()
    await store.post(
        Comment(ticket_id="T1", author="dev-1", content="chat")
    )
    await store.post(
        Comment(
            ticket_id="T1",
            author="dev-1",
            content="phase ran",
            kind="phase_run",
            phase_result="success",
            phase_branch="feat/foo",
        )
    )
    runs = await store.phase_runs_for("T1")
    assert len(runs) == 1
    assert runs[0].phase_result == "success"


@pytest.mark.asyncio
async def test_commits_for_ticket(tmp_path: Path) -> None:
    store = CommentStore(tmp_path / "comments.jsonl")
    await store.load()
    await store.post(
        Comment(
            ticket_id="T1",
            author="dev-1",
            content="commit abc",
            kind="commit",
            commit_sha="abc123",
        )
    )
    commits = await store.commits_for("T1")
    assert len(commits) == 1
    assert commits[0].commit_sha == "abc123"


@pytest.mark.asyncio
async def test_for_ticket_is_chronological(tmp_path: Path) -> None:
    store = CommentStore(tmp_path / "comments.jsonl")
    await store.load()
    from datetime import datetime, timezone, timedelta
    t0 = datetime.now(timezone.utc)
    await store.post(
        Comment(
            ticket_id="T1",
            author="dev-1",
            content="second",
            created_at=t0 + timedelta(seconds=1),
        )
    )
    await store.post(
        Comment(ticket_id="T1", author="dev-1", content="first", created_at=t0)
    )
    comments = await store.for_ticket("T1")
    assert [c.content for c in comments] == ["first", "second"]
