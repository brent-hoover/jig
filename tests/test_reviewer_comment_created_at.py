"""Tests for ``ReviewerComment.created_at`` — fix-loop-context step 1.

The field is added so consumers (``jig story``, TUI ack history) have a
real timestamp to display. Stable-ID ordering uses insertion order, not
this field — these tests pin the timestamp semantics so the related
contracts (sentinel for legacy, write-time stamp for new, no
load-time mutation) don't regress.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


from jig.reviewers.comment import ReviewerComment, ReviewerCommentType, Severity
from jig.store.review_comments import ReviewCommentsStore

UTC = timezone.utc
SENTINEL = datetime(1, 1, 1, tzinfo=UTC)


def _make(**overrides) -> dict:
    payload: dict = {
        "type": ReviewerCommentType.PATTERN_DIVERGENCE,
        "severity": Severity.IMPORTANT,
        "reviewer": "reviewer-pattern-conformance",
        "prose": "fixture comment",
        "ticket_id": "t-1",
    }
    payload.update(overrides)
    return payload


class TestCreatedAtField:
    def test_default_is_aware_sentinel(self) -> None:
        """Missing field on load → static default. Must be tz-aware so it
        can be compared with `datetime.now(UTC)` without crashing."""
        c = ReviewerComment.model_validate(_make())
        assert c.created_at == SENTINEL
        assert c.created_at.tzinfo is not None

    def test_explicit_value_preserved(self) -> None:
        """If the payload supplies created_at, the model keeps it."""
        explicit = datetime(2026, 5, 19, 12, 0, tzinfo=UTC)
        c = ReviewerComment.model_validate(_make(created_at=explicit))
        assert c.created_at == explicit

    def test_two_loads_produce_same_timestamp(self) -> None:
        """Loading the same row twice must NOT shift created_at forward.
        Catches the default_factory regression (factory would stamp the
        load time on legacy rows, rewriting history)."""
        raw = _make()
        first = ReviewerComment.model_validate(raw)
        second = ReviewerComment.model_validate(raw)
        assert first.created_at == second.created_at == SENTINEL


class TestStoreStampsOnAppend:
    """``ReviewCommentsStore.append`` is the single stamping point —
    every write path (MCP, dispatch, per_commit_runner, eval, sim) goes
    through it. So this test pins behavior for every caller at once."""

    async def test_append_replaces_sentinel_with_now(self, tmp_path: Path) -> None:
        store = ReviewCommentsStore(tmp_path / "rc.jsonl")
        await store.load()
        before = datetime.now(UTC)
        comment = ReviewerComment.model_validate(_make())
        # Confirm we're starting from the sentinel — without that,
        # the test would be trivially green.
        assert comment.created_at == SENTINEL
        await store.append(comment)
        after = datetime.now(UTC)

        rows = await store.for_ticket("t-1")
        assert len(rows) == 1
        ts = rows[0].created_at
        assert before - timedelta(seconds=2) <= ts <= after + timedelta(seconds=2)
        assert ts != SENTINEL

    async def test_append_preserves_explicit_timestamp(self, tmp_path: Path) -> None:
        """A caller that knows what it's doing (e.g., a backfill or
        replay) supplies a real timestamp; the store must not overwrite."""
        store = ReviewCommentsStore(tmp_path / "rc.jsonl")
        await store.load()
        explicit = datetime(2026, 5, 19, 12, 0, tzinfo=UTC)
        await store.append(ReviewerComment.model_validate(_make(created_at=explicit)))
        rows = await store.for_ticket("t-1")
        assert rows[0].created_at == explicit


class TestLegacyRowsLoadWithSentinel:
    """Legacy JSONL rows written before this field existed must load
    cleanly with the sentinel — no crash, no load-time mutation."""

    async def test_legacy_jsonl_row_loads_with_sentinel(self, tmp_path: Path) -> None:
        store_path = tmp_path / "rc.jsonl"
        legacy_row = {
            "_op": "insert",
            "_id": "rc-legacy-1",
            "type": "pattern-divergence",
            "severity": "important",
            "reviewer": "reviewer-pattern-conformance",
            "prose": "legacy comment without created_at",
            "ticket_id": "t-1",
        }
        store_path.write_text(json.dumps(legacy_row) + "\n")

        store = ReviewCommentsStore(store_path)
        await store.load()
        rows = await store.for_ticket("t-1")
        assert len(rows) == 1
        assert rows[0].created_at == SENTINEL

    async def test_load_twice_does_not_mutate_legacy_timestamp(
        self, tmp_path: Path
    ) -> None:
        """The load-twice invariant from the design — defends against a
        future default_factory regression that would stamp now() on every
        read of legacy rows."""
        store_path = tmp_path / "rc.jsonl"
        legacy_row = {
            "_op": "insert",
            "_id": "rc-legacy-1",
            "type": "pattern-divergence",
            "severity": "important",
            "reviewer": "reviewer-pattern-conformance",
            "prose": "legacy comment",
            "ticket_id": "t-1",
        }
        store_path.write_text(json.dumps(legacy_row) + "\n")

        first = ReviewCommentsStore(store_path)
        await first.load()
        ts1 = (await first.for_ticket("t-1"))[0].created_at

        second = ReviewCommentsStore(store_path)
        await second.load()
        ts2 = (await second.for_ticket("t-1"))[0].created_at

        assert ts1 == ts2 == SENTINEL


class TestNonMcpAppendStampsToo:
    """The store-level stamp covers callers that bypass reviewer_mcp.
    Mimics the shape of writes from ``jig/reviewers/dispatch.py`` and
    ``jig/hooks/per_commit_runner.py`` to confirm they get a real
    timestamp without needing to know about stamping."""

    async def test_direct_append_via_dispatch_shape(self, tmp_path: Path) -> None:
        store = ReviewCommentsStore(tmp_path / "rc.jsonl")
        await store.load()
        # Simulate dispatch.py: construct the model directly, no MCP
        # payload pre-processing. Field is at its default sentinel.
        comment = ReviewerComment(
            type=ReviewerCommentType.PATTERN_DIVERGENCE,
            severity=Severity.IMPORTANT,
            reviewer="reviewer-pattern-conformance",
            prose="direct-append shape",
            ticket_id="t-1",
        )
        assert comment.created_at == SENTINEL

        before = datetime.now(UTC)
        await store.append(comment)
        rows = await store.for_ticket("t-1")
        assert len(rows) == 1
        assert rows[0].created_at >= before - timedelta(seconds=2)
        assert rows[0].created_at != SENTINEL
