"""Tests for ``ReviewCommentsStore`` (Track G MVP follow-on).

Covers append + per-ticket / per-cycle filtering and the
``ticket_id`` / ``cycle`` model-field additions on
``ReviewerComment``.
"""

from __future__ import annotations

from pathlib import Path

from jig.reviewers.comment import (
    ReviewerComment,
    ReviewerCommentType,
    Severity,
)
from jig.store.review_comments import ReviewCommentsStore


def _make_comment(
    *,
    ticket_id: str | None = "tkt-1",
    cycle: int = 0,
    type_: ReviewerCommentType = ReviewerCommentType.EMPTY_DIFF,
    reviewer: str = "contract-compliance",
    severity: Severity = Severity.CRITICAL,
) -> ReviewerComment:
    return ReviewerComment(
        type=type_,
        severity=severity,
        reviewer=reviewer,
        prose="finding: dev agent didn't produce reviewable diff",
        ticket_id=ticket_id,
        cycle=cycle,
    )


async def _store(tmp_path: Path) -> ReviewCommentsStore:
    store = ReviewCommentsStore(tmp_path / "review_comments.jsonl")
    await store.load()
    return store


class TestModelFields:
    def test_ticket_id_defaults_to_none(self) -> None:
        c = _make_comment(ticket_id=None)
        assert c.ticket_id is None

    def test_cycle_defaults_to_zero(self) -> None:
        c = ReviewerComment(
            type=ReviewerCommentType.EMPTY_DIFF,
            severity=Severity.CRITICAL,
            reviewer="contract-compliance",
            prose="x",
        )
        assert c.cycle == 0
        assert c.ticket_id is None

    def test_cycle_must_be_non_negative(self) -> None:
        import pytest

        with pytest.raises(Exception):
            _make_comment(cycle=-1)


class TestRoundtrip:
    async def test_append_and_get(self, tmp_path: Path) -> None:
        store = await _store(tmp_path)
        cid = await store.append(_make_comment())
        loaded = await store.get(cid)
        assert loaded is not None
        assert loaded.ticket_id == "tkt-1"
        assert loaded.reviewer == "contract-compliance"
        # use_enum_values=True dumps as the string value
        assert loaded.type == ReviewerCommentType.EMPTY_DIFF.value

    async def test_get_missing_returns_none(self, tmp_path: Path) -> None:
        store = await _store(tmp_path)
        assert await store.get("does-not-exist") is None


class TestQueries:
    async def test_for_ticket_filters_by_ticket(self, tmp_path: Path) -> None:
        store = await _store(tmp_path)
        await store.append(_make_comment(ticket_id="tkt-1"))
        await store.append(_make_comment(ticket_id="tkt-2"))
        await store.append(_make_comment(ticket_id="tkt-1"))
        got = await store.for_ticket("tkt-1")
        assert len(got) == 2
        assert all(c.ticket_id == "tkt-1" for c in got)

    async def test_for_cycle_slices(self, tmp_path: Path) -> None:
        store = await _store(tmp_path)
        await store.append(_make_comment(ticket_id="tkt-1", cycle=0))
        await store.append(_make_comment(ticket_id="tkt-1", cycle=1))
        await store.append(_make_comment(ticket_id="tkt-1", cycle=1))
        await store.append(_make_comment(ticket_id="tkt-1", cycle=2))
        cycle1 = await store.for_cycle("tkt-1", 1)
        assert len(cycle1) == 2
        assert all(c.cycle == 1 for c in cycle1)

    async def test_for_ticket_returns_empty_when_absent(self, tmp_path: Path) -> None:
        store = await _store(tmp_path)
        assert await store.for_ticket("tkt-missing") == []


class TestPersistence:
    async def test_reload_reads_jsonl(self, tmp_path: Path) -> None:
        store = await _store(tmp_path)
        await store.append(_make_comment(ticket_id="tkt-9", cycle=2))
        store2 = ReviewCommentsStore(tmp_path / "review_comments.jsonl")
        await store2.load()
        got = await store2.for_ticket("tkt-9")
        assert len(got) == 1
        assert got[0].cycle == 2
