"""Tests for the bounded fix-loop tracker (Track G MVP follow-on).

Covers cycle accumulation + recurrence detection (consecutive-cycles
rule) + analytics emission via BoundedFixLoopExhausted.
"""

from __future__ import annotations

from pathlib import Path

from jig.analytics.emitter import EventEmitter
from jig.analytics.events import BoundedFixLoopExhausted
from jig.analytics.store import AnalyticsStore
from jig.reviewers.comment import (
    ReviewerComment,
    ReviewerCommentType,
    Severity,
)
from jig.reviewers.fix_loop import FixLoop, category_histogram
from jig.store.review_comments import ReviewCommentsStore


def _comment(
    type_: ReviewerCommentType = ReviewerCommentType.CONTRACT_VIOLATION,
    *,
    reviewer: str = "contract-compliance",
    severity: Severity = Severity.IMPORTANT,
    confidence: float = 1.0,
) -> ReviewerComment:
    return ReviewerComment(
        type=type_,
        severity=severity,
        reviewer=reviewer,
        prose="finding",
        confidence=confidence,
    )


async def _store(tmp_path: Path) -> ReviewCommentsStore:
    s = ReviewCommentsStore(tmp_path / "review_comments.jsonl")
    await s.load()
    return s


async def _emitter(tmp_path: Path) -> EventEmitter:
    a = AnalyticsStore(tmp_path / "events.jsonl")
    await a.load()
    return EventEmitter(a, simulator_mode=False)


class TestRecordCycle:
    async def test_first_cycle_returns_zero(self, tmp_path: Path) -> None:
        loop = FixLoop(await _store(tmp_path))
        n = await loop.record_cycle("tkt-1", [_comment()])
        assert n == 0

    async def test_cycles_increment(self, tmp_path: Path) -> None:
        loop = FixLoop(await _store(tmp_path))
        await loop.record_cycle("tkt-1", [_comment()])
        n2 = await loop.record_cycle("tkt-1", [_comment()])
        n3 = await loop.record_cycle("tkt-1", [_comment()])
        assert n2 == 1
        assert n3 == 2

    async def test_stamps_ticket_id_and_cycle(self, tmp_path: Path) -> None:
        store = await _store(tmp_path)
        loop = FixLoop(store)
        # Comment passed in has wrong ticket_id/cycle; tracker overrides.
        c = _comment().model_copy(update={"ticket_id": "wrong", "cycle": 99})
        await loop.record_cycle("tkt-7", [c])
        rows = await store.for_ticket("tkt-7")
        assert len(rows) == 1
        assert rows[0].cycle == 0
        assert rows[0].ticket_id == "tkt-7"

    async def test_cycles_isolated_per_ticket(self, tmp_path: Path) -> None:
        loop = FixLoop(await _store(tmp_path))
        await loop.record_cycle("a", [_comment(), _comment()])
        n_b = await loop.record_cycle("b", [_comment()])
        assert n_b == 0


class TestExhaustionDetection:
    async def test_no_exhaustion_below_cap(self, tmp_path: Path) -> None:
        loop = FixLoop(await _store(tmp_path))
        await loop.record_cycle("t", [_comment(ReviewerCommentType.CONTRACT_VIOLATION)])
        await loop.record_cycle("t", [_comment(ReviewerCommentType.CONTRACT_VIOLATION)])
        exhausted, cats = await loop.is_exhausted("t", max_cycles=3)
        assert exhausted is False
        assert cats == []

    async def test_exhaustion_when_category_recurs_three_consecutive(
        self, tmp_path: Path
    ) -> None:
        loop = FixLoop(await _store(tmp_path))
        for _ in range(3):
            await loop.record_cycle(
                "t", [_comment(ReviewerCommentType.CONTRACT_VIOLATION)]
            )
        exhausted, cats = await loop.is_exhausted("t", max_cycles=3)
        assert exhausted is True
        assert cats == [ReviewerCommentType.CONTRACT_VIOLATION.value]

    async def test_no_exhaustion_when_category_skips_a_cycle(
        self, tmp_path: Path
    ) -> None:
        # Category present in 0, then absent in 1, then present in 2, 3.
        # The 3-consecutive window 1, 2, 3 has the category in only 2/3
        # so it does not trip.
        loop = FixLoop(await _store(tmp_path))
        await loop.record_cycle("t", [_comment(ReviewerCommentType.CONTRACT_VIOLATION)])
        await loop.record_cycle("t", [_comment(ReviewerCommentType.EMPTY_DIFF)])
        await loop.record_cycle("t", [_comment(ReviewerCommentType.CONTRACT_VIOLATION)])
        await loop.record_cycle("t", [_comment(ReviewerCommentType.CONTRACT_VIOLATION)])
        exhausted, cats = await loop.is_exhausted("t", max_cycles=3)
        assert exhausted is False
        assert cats == []

    async def test_exhaustion_with_window_later_in_history(
        self, tmp_path: Path
    ) -> None:
        # Cycles 0, 1 have category A; 2, 3, 4 have category B.
        # A 3-consecutive window 2..4 catches B as recurring.
        loop = FixLoop(await _store(tmp_path))
        await loop.record_cycle("t", [_comment(ReviewerCommentType.EMPTY_DIFF)])
        await loop.record_cycle("t", [_comment(ReviewerCommentType.EMPTY_DIFF)])
        for _ in range(3):
            await loop.record_cycle(
                "t", [_comment(ReviewerCommentType.CONTRACT_VIOLATION)]
            )
        exhausted, cats = await loop.is_exhausted("t", max_cycles=3)
        assert exhausted is True
        assert cats == [ReviewerCommentType.CONTRACT_VIOLATION.value]

    async def test_returns_all_recurring_categories(
        self, tmp_path: Path
    ) -> None:
        loop = FixLoop(await _store(tmp_path))
        for _ in range(3):
            await loop.record_cycle(
                "t",
                [
                    _comment(ReviewerCommentType.CONTRACT_VIOLATION),
                    _comment(ReviewerCommentType.EMPTY_DIFF),
                ],
            )
        exhausted, cats = await loop.is_exhausted("t", max_cycles=3)
        assert exhausted is True
        assert set(cats) == {
            ReviewerCommentType.CONTRACT_VIOLATION.value,
            ReviewerCommentType.EMPTY_DIFF.value,
        }


class TestEmitExhaustionEvent:
    async def test_emits_event_with_payload(self, tmp_path: Path) -> None:
        emitter = await _emitter(tmp_path)
        eid = await FixLoop.emit_exhaustion_event(
            ticket_id="tkt-1",
            recurring_categories=[ReviewerCommentType.CONTRACT_VIOLATION.value],
            dev_agent_reason="cannot_resolve",
            emitter=emitter,
            cycles_attempted=3,
            reviewer_roles_involved=["contract-compliance"],
        )
        assert eid

        store = AnalyticsStore(tmp_path / "events.jsonl")
        await store.load()
        events = await store.by_kind("bounded_fix_loop_exhausted")
        assert len(events) == 1
        ev = events[0]
        assert isinstance(ev, BoundedFixLoopExhausted)
        assert ev.ticket_id == "tkt-1"
        assert ev.cycles_attempted == 3
        assert ev.recurring_comment_categories == [
            ReviewerCommentType.CONTRACT_VIOLATION.value
        ]
        assert ev.escalation_outcome == "still_open"


class TestCategoryHistogram:
    def test_counts_by_type(self) -> None:
        hist = category_histogram(
            [
                _comment(ReviewerCommentType.CONTRACT_VIOLATION),
                _comment(ReviewerCommentType.CONTRACT_VIOLATION),
                _comment(ReviewerCommentType.EMPTY_DIFF),
            ]
        )
        assert hist == {
            ReviewerCommentType.CONTRACT_VIOLATION.value: 2,
            ReviewerCommentType.EMPTY_DIFF.value: 1,
        }
