"""Tests for ``jig story`` finding-event interleaving —
fix-loop-context step 6."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from jig.reviewers.comment import ReviewerComment, ReviewerCommentType, Severity
from jig.store.finding_acks import FindingAck, FindingAcksStore
from jig.store.review_comments import ReviewCommentsStore
from jig.store.threads import ThreadStore
from jig.story import StorySource, build_story
from jig.thread import Note

UTC = timezone.utc


async def _seed_thread(threads: ThreadStore, ticket_id: str, *, when: datetime) -> None:
    """Post a Note with a specific timestamp to give the story a
    thread anchor."""
    note = Note(ticket_id=ticket_id, author="dev", text="thread anchor")
    # Bypass auto-stamping by post + then manually adjusting via the
    # underlying collection if needed. For test purposes, we use the
    # default created_at and assert relative ordering.
    await threads.post(note)


async def test_finding_events_appear_in_story(tmp_path: Path) -> None:
    project = tmp_path / "p"
    store_dir = project / ".jig" / "store"
    store_dir.mkdir(parents=True)

    rc_store = ReviewCommentsStore(store_dir / "review_comments.jsonl")
    await rc_store.load()
    await rc_store.append(
        ReviewerComment(
            type=ReviewerCommentType.PATTERN_DIVERGENCE,
            severity=Severity.IMPORTANT,
            reviewer="reviewer-pattern-conformance",
            prose="duplicate constant",
            file="src/main.py",
            line=23,
            ticket_id="t-1",
            cycle=0,
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
            prose="removed the duplicate",
        )
    )

    threads = ThreadStore(store_dir / "threads.jsonl")
    await threads.load()

    events = await build_story(
        "t-1",
        project_path=project,
        threads=threads,
    )

    # Two finding events: one raised, one addressed.
    finding_events = [e for e in events if e.source == StorySource.finding]
    assert len(finding_events) == 2
    kinds = sorted(e.kind for e in finding_events)
    assert kinds == ["finding_addressed", "finding_raised"]


async def test_finding_message_includes_id_and_prose(tmp_path: Path) -> None:
    project = tmp_path / "p"
    store_dir = project / ".jig" / "store"
    store_dir.mkdir(parents=True)

    rc_store = ReviewCommentsStore(store_dir / "review_comments.jsonl")
    await rc_store.load()
    await rc_store.append(
        ReviewerComment(
            type=ReviewerCommentType.PATTERN_DIVERGENCE,
            severity=Severity.IMPORTANT,
            reviewer="reviewer-pattern-conformance",
            prose="_HN_BASE duplicates BASE in api.py",
            file="src/main.py",
            line=23,
            ticket_id="t-1",
        )
    )

    threads = ThreadStore(store_dir / "threads.jsonl")
    await threads.load()
    events = await build_story("t-1", project_path=project, threads=threads)
    raised = [e for e in events if e.kind == "finding_raised"]
    assert raised
    msg = raised[0].message
    assert "RC-1" in msg
    assert "src/main.py" in msg
    assert "_HN_BASE duplicates BASE" in msg


async def test_no_stores_yields_no_finding_events(tmp_path: Path) -> None:
    """Story still works when neither store file exists (legacy
    projects with no review activity)."""
    project = tmp_path / "p"
    (project / ".jig" / "store").mkdir(parents=True)

    threads = ThreadStore(project / ".jig" / "store" / "threads.jsonl")
    await threads.load()

    events = await build_story("t-1", project_path=project, threads=threads)
    finding_events = [e for e in events if e.source == StorySource.finding]
    assert finding_events == []


async def test_findings_interleave_with_threads_by_timestamp(
    tmp_path: Path,
) -> None:
    project = tmp_path / "p"
    store_dir = project / ".jig" / "store"
    store_dir.mkdir(parents=True)

    threads = ThreadStore(store_dir / "threads.jsonl")
    await threads.load()

    rc_store = ReviewCommentsStore(store_dir / "review_comments.jsonl")
    await rc_store.load()

    # Note posted first (and auto-stamped), then finding raised later.
    await threads.post(Note(ticket_id="t-1", author="dev", text="early note"))
    await rc_store.append(
        ReviewerComment(
            type=ReviewerCommentType.PATTERN_DIVERGENCE,
            severity=Severity.IMPORTANT,
            reviewer="rev",
            prose="later finding",
            file="src/a.py",
            line=1,
            ticket_id="t-1",
        )
    )

    events = await build_story("t-1", project_path=project, threads=threads)
    # Both events should be present, sorted by timestamp.
    # Thread event ts < finding event ts → thread first.
    sources = [e.source for e in events]
    thread_idx = sources.index(StorySource.thread)
    finding_idx = sources.index(StorySource.finding)
    assert thread_idx < finding_idx
