"""Tests for the ``get_findings`` WebSocket command —
fix-loop-context step 7."""

from __future__ import annotations

from pathlib import Path

from jig.events import EventEmitter
from jig.reviewers.comment import ReviewerComment, ReviewerCommentType, Severity
from jig.store.finding_acks import FindingAck, FindingAcksStore
from jig.store.review_comments import ReviewCommentsStore
from jig.ws_server import WebSocketServer


def _server(project_path: Path) -> WebSocketServer:
    server = WebSocketServer(
        emitter=EventEmitter(),
        port=0,
        orchestrator=None,
        project_path=project_path,
    )
    return server


async def test_empty_when_no_stores(tmp_path: Path) -> None:
    project = tmp_path / "p"
    (project / ".jig" / "store").mkdir(parents=True)
    server = _server(project)
    out = await server._get_findings_for_ticket("t-1")
    assert out == []


async def test_returns_finding_with_open_status(tmp_path: Path) -> None:
    project = tmp_path / "p"
    store_dir = project / ".jig" / "store"
    store_dir.mkdir(parents=True)
    rc_store = ReviewCommentsStore(store_dir / "review_comments.jsonl")
    await rc_store.load()
    await rc_store.append(
        ReviewerComment(
            type=ReviewerCommentType.PATTERN_DIVERGENCE,
            severity=Severity.IMPORTANT,
            reviewer="rev",
            prose="bug",
            file="src/a.py",
            line=10,
            ticket_id="t-1",
        )
    )
    server = _server(project)
    out = await server._get_findings_for_ticket("t-1")
    assert len(out) == 1
    assert out[0]["finding_id"] == "RC-1"
    assert out[0]["status"] == "open"
    assert out[0]["ack_history"] == []


async def test_status_addressed(tmp_path: Path) -> None:
    project = tmp_path / "p"
    store_dir = project / ".jig" / "store"
    store_dir.mkdir(parents=True)
    rc_store = ReviewCommentsStore(store_dir / "review_comments.jsonl")
    await rc_store.load()
    await rc_store.append(
        ReviewerComment(
            type=ReviewerCommentType.PATTERN_DIVERGENCE,
            severity=Severity.IMPORTANT,
            reviewer="rev",
            prose="bug",
            file="src/a.py",
            line=10,
            ticket_id="t-1",
        )
    )
    acks_store = FindingAcksStore(store_dir / "finding_acks.jsonl")
    await acks_store.load()
    await acks_store.append(
        FindingAck(
            ticket_id="t-1",
            finding_id="RC-1",
            kind="addressed",
            author="dev",
            cycle=1,
            prose="fixed",
        )
    )
    server = _server(project)
    out = await server._get_findings_for_ticket("t-1")
    assert out[0]["status"] == "addressed"
    assert len(out[0]["ack_history"]) == 1


async def test_status_resolved(tmp_path: Path) -> None:
    project = tmp_path / "p"
    store_dir = project / ".jig" / "store"
    store_dir.mkdir(parents=True)
    rc_store = ReviewCommentsStore(store_dir / "review_comments.jsonl")
    await rc_store.load()
    await rc_store.append(
        ReviewerComment(
            type=ReviewerCommentType.PATTERN_DIVERGENCE,
            severity=Severity.IMPORTANT,
            reviewer="rev",
            prose="bug",
            file="src/a.py",
            line=10,
            ticket_id="t-1",
        )
    )
    acks_store = FindingAcksStore(store_dir / "finding_acks.jsonl")
    await acks_store.load()
    await acks_store.append(
        FindingAck(
            ticket_id="t-1",
            finding_id="RC-1",
            kind="addressed",
            author="dev",
            cycle=1,
            prose="fixed",
        )
    )
    await acks_store.append(
        FindingAck(
            ticket_id="t-1",
            finding_id="RC-1",
            kind="resolved",
            author="reviewer",
            cycle=2,
            prose="confirmed",
        )
    )
    server = _server(project)
    out = await server._get_findings_for_ticket("t-1")
    assert out[0]["status"] == "resolved"
