"""Bounded review-fix loop tracker.

Per ``docs/pm-workflow/design.md`` section "Bounded fix loops": the
review-fix iteration is capped at 3 cycles. After 3 cycles where the
same comment category keeps recurring, the ticket stalls and the
operator gets escalation via ``BoundedFixLoopExhausted`` analytics.

Why 3? The design's heuristic: 1 cycle is "the dev agent missed it,"
2 is "the dev agent's first fix was wrong," 3 is "the dev agent
isn't converging on this category and a different actor is needed
(SA contract amend, Planner resplit, or operator override)."

A category "recurs" if it appears in 3+ **consecutive** cycles
(per spec). Three cycles where category X appears in cycles 0, 2, 4
do NOT trigger the cap; X must appear in 0, 1, 2 (or 1, 2, 3, etc.).

The tracker is stateless across instantiations — every query reads
the underlying ``ReviewCommentsStore``. Wiring into the orchestrator
is a separate hook task; this lands the reusable detector +
emission helper.
"""

from __future__ import annotations

from collections import Counter

from jig.analytics.emitter import EventEmitter
from jig.analytics.events import BoundedFixLoopExhausted
from jig.reviewers.comment import ReviewerComment
from jig.store.review_comments import ReviewCommentsStore


class FixLoop:
    """Cycle-aware view over a ``ReviewCommentsStore``.

    Construct one per ticket pass; the same instance can record
    multiple cycles as the dev agent re-submits.
    """

    def __init__(self, store: ReviewCommentsStore) -> None:
        self._store = store

    # ---- writes ---------------------------------------------------------

    async def record_cycle(
        self,
        ticket_id: str,
        comments: list[ReviewerComment],
    ) -> int:
        """Persist this cycle's comments and return the new cycle number.

        Auto-increments based on what's already in the store so callers
        don't have to track cycle counters externally. The first
        ``record_cycle`` for a ticket returns ``0``; the next returns
        ``1``; etc. Each comment is stamped with the resolved cycle
        number before persisting (overrides any pre-set ``cycle``
        on the input — the tracker is the canonical authority).
        """
        existing = await self._store.for_ticket(ticket_id)
        next_cycle = (max((c.cycle for c in existing), default=-1)) + 1
        for comment in comments:
            stamped = comment.model_copy(
                update={"ticket_id": ticket_id, "cycle": next_cycle}
            )
            await self._store.append(stamped)
        return next_cycle

    # ---- reads ----------------------------------------------------------

    async def is_exhausted(
        self,
        ticket_id: str,
        max_cycles: int = 3,
    ) -> tuple[bool, list[str]]:
        """Return ``(yes/no, recurring_categories)`` for ``ticket_id``.

        Recurrence: a category present in ``max_cycles`` **consecutive**
        cycles. ``max_cycles=3`` is the design default.

        Returns ``(False, [])`` when the ticket has fewer than
        ``max_cycles`` cycles recorded — the cap can't trip until
        that many passes have run.
        """
        comments = await self._store.for_ticket(ticket_id)
        if not comments:
            return False, []

        # Group categories by cycle. Comments without a category /
        # without a cycle (legacy rows) are treated as their own bucket;
        # they don't contribute to recurrence detection.
        by_cycle: dict[int, set[str]] = {}
        for c in comments:
            by_cycle.setdefault(c.cycle, set()).add(str(c.type))

        cycles_sorted = sorted(by_cycle)
        if len(cycles_sorted) < max_cycles:
            return False, []

        # Slide a window of size ``max_cycles`` over the cycles in
        # order. Within each window, find categories present in
        # every member; those are the recurring set for that window.
        recurring: set[str] = set()
        for i in range(len(cycles_sorted) - max_cycles + 1):
            window = cycles_sorted[i : i + max_cycles]
            # The cycles must be consecutive integers to count.
            if window[-1] - window[0] != max_cycles - 1:
                continue
            common = set.intersection(*(by_cycle[c] for c in window))
            recurring |= common

        return bool(recurring), sorted(recurring)

    # ---- emission helper ------------------------------------------------

    @staticmethod
    async def emit_exhaustion_event(
        ticket_id: str,
        recurring_categories: list[str],
        dev_agent_reason: str | None,
        emitter: EventEmitter,
        *,
        cycles_attempted: int = 3,
        reviewer_roles_involved: list[str] | None = None,
        escalation_outcome: str = "still_open",
    ) -> str:
        """Build + emit a ``BoundedFixLoopExhausted`` event.

        Returns the event id. Uses the synchronous ``emit`` rather
        than ``emit_nowait`` because the cap-hit moment is exactly
        when callers want to confirm the event landed before they
        kick off the operator-escalation flow.
        """
        # The escalation_outcome enum is constrained by the schema;
        # callers pass the literal value. The default ``still_open``
        # reflects the moment the cap trips — operator action follows.
        event = BoundedFixLoopExhausted(
            ticket_id=ticket_id,
            cycles_attempted=cycles_attempted,
            recurring_comment_categories=list(recurring_categories),
            reviewer_roles_involved=list(reviewer_roles_involved or []),
            dev_agent_stated_reason=dev_agent_reason,
            escalation_outcome=escalation_outcome,  # type: ignore[arg-type]
        )
        return await emitter.emit(event)


# ---- convenience: category counter ----------------------------------------


def category_histogram(comments: list[ReviewerComment]) -> dict[str, int]:
    """Return ``{category: count}`` across the comments.

    Useful for the analytics consumer that wants to see "of the
    recurring categories at exhaustion, which dominated?" without
    re-iterating over the store.
    """
    return dict(Counter(str(c.type) for c in comments))


__all__ = [
    "FixLoop",
    "category_histogram",
]
