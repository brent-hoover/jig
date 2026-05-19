"""Tests for ``ReviewCommentsStore.for_ticket_chronological`` —
fix-loop-context step 1.

The new ordered-read walks the in-memory ``_docs`` dict directly
(Python dict insertion order, deterministic). It exists because the
legacy ``for_ticket`` goes through a set-backed index, which has
non-deterministic iteration order across processes. Stable RC-N IDs
depend on this ordering being deterministic.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from jig.reviewers.comment import ReviewerComment, ReviewerCommentType, Severity
from jig.store.review_comments import ReviewCommentsStore

UTC = timezone.utc


def _comment(reviewer: str, prose: str, ticket_id: str = "t-1") -> ReviewerComment:
    return ReviewerComment(
        type=ReviewerCommentType.PATTERN_DIVERGENCE,
        severity=Severity.IMPORTANT,
        reviewer=reviewer,
        prose=prose,
        ticket_id=ticket_id,
    )


class TestChronologicalOrder:
    async def test_returns_rows_in_append_order(self, tmp_path: Path) -> None:
        store = ReviewCommentsStore(tmp_path / "rc.jsonl")
        await store.load()
        await store.append(_comment("a", "first"))
        await store.append(_comment("b", "second"))
        await store.append(_comment("c", "third"))
        rows = await store.for_ticket_chronological("t-1")
        assert [r.prose for r in rows] == ["first", "second", "third"]

    async def test_filters_by_ticket(self, tmp_path: Path) -> None:
        store = ReviewCommentsStore(tmp_path / "rc.jsonl")
        await store.load()
        await store.append(_comment("a", "first", ticket_id="t-1"))
        await store.append(_comment("a", "other", ticket_id="t-2"))
        await store.append(_comment("a", "second", ticket_id="t-1"))
        rows = await store.for_ticket_chronological("t-1")
        assert [r.prose for r in rows] == ["first", "second"]

    async def test_ordering_ignores_created_at(self, tmp_path: Path) -> None:
        """Insertion order, not created_at, drives the result. A row
        with an earlier created_at appended *later* still sorts last."""
        store = ReviewCommentsStore(tmp_path / "rc.jsonl")
        await store.load()
        late_explicit = datetime(2030, 1, 1, tzinfo=UTC)
        early_explicit = datetime(2020, 1, 1, tzinfo=UTC)
        await store.append(
            ReviewerComment(
                type=ReviewerCommentType.PATTERN_DIVERGENCE,
                severity=Severity.IMPORTANT,
                reviewer="a",
                prose="appended-first-but-future-dated",
                ticket_id="t-1",
                created_at=late_explicit,
            )
        )
        await store.append(
            ReviewerComment(
                type=ReviewerCommentType.PATTERN_DIVERGENCE,
                severity=Severity.IMPORTANT,
                reviewer="a",
                prose="appended-second-but-past-dated",
                ticket_id="t-1",
                created_at=early_explicit,
            )
        )
        rows = await store.for_ticket_chronological("t-1")
        assert [r.prose for r in rows] == [
            "appended-first-but-future-dated",
            "appended-second-but-past-dated",
        ]


class TestDeterministicAcrossLoads:
    """Critical: stable-ID assignment depends on this ordering being
    the same on every load. Build a JSONL with deliberate id-hash
    perturbation and confirm the chronological read is unchanged."""

    async def test_round_trip_preserves_order(self, tmp_path: Path) -> None:
        store_path = tmp_path / "rc.jsonl"
        # Seed with ids whose hash order is unlikely to match append
        # order; ensures we're not accidentally relying on hash iteration.
        lines = []
        for i, prose in enumerate(
            ["zzz-first", "yyy-second", "xxx-third", "www-fourth"]
        ):
            row = {
                "_op": "insert",
                "_id": f"rc-{prose}",
                "type": "pattern-divergence",
                "severity": "important",
                "reviewer": "a",
                "prose": prose,
                "ticket_id": "t-1",
                "created_at": datetime(2026, 5, 19, 12, i, tzinfo=UTC).isoformat(),
            }
            lines.append(json.dumps(row))
        store_path.write_text("\n".join(lines) + "\n")

        first = ReviewCommentsStore(store_path)
        await first.load()
        result_1 = [r.prose for r in await first.for_ticket_chronological("t-1")]

        second = ReviewCommentsStore(store_path)
        await second.load()
        result_2 = [r.prose for r in await second.for_ticket_chronological("t-1")]

        assert result_1 == result_2
        assert result_1 == ["zzz-first", "yyy-second", "xxx-third", "www-fourth"]


class TestLegacyForTicketUnchanged:
    """The existing for_ticket method is kept (callers that don't care
    about order still work). Just confirm it still returns the right
    rows; we don't constrain its ordering."""

    async def test_for_ticket_still_returns_same_set(self, tmp_path: Path) -> None:
        store = ReviewCommentsStore(tmp_path / "rc.jsonl")
        await store.load()
        await store.append(_comment("a", "first"))
        await store.append(_comment("b", "second"))
        legacy = await store.for_ticket("t-1")
        chronological = await store.for_ticket_chronological("t-1")
        assert sorted(c.prose for c in legacy) == sorted(c.prose for c in chronological)
