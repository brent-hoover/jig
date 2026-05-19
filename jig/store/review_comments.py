"""ReviewCommentsStore — append-JSONL storage for reviewer comments.

Mirrors :class:`jig.store.check_results.CheckResultsStore` — Collection-
backed, indexed on ``ticket_id`` so the lead-reviewer agent (deferred)
and bounded-fix-loop tracker can ask "what did reviewers say about
ticket X?" without scanning the whole file.

Per the F MVP follow-on note: the lead-reviewer agent and analytics
events need a typed, queryable surface for the structured comments
the federation produces. Bones reviewers returned ``list[ReviewerComment]``
in-memory; persisting through this store closes the loop so per-cycle
filtering and exhaustion detection have something to read.

Records are append-only. A re-issue of the "same" comment in a later
cycle is a fresh row — the audit trail keeps every cycle's findings
visible. ``for_cycle`` is the per-cycle slicing query the bounded fix
loop calls; ``for_ticket`` is the full history.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from jig.reviewers.comment import ReviewerComment
from jig.store.collection import Collection

_CREATED_AT_SENTINEL = datetime(1, 1, 1, tzinfo=timezone.utc)


class ReviewCommentsStore:
    """Append-only JSONL store for ``ReviewerComment`` records.

    Indexed on ``ticket_id`` and ``cycle`` so the bounded fix loop can
    cheaply pull a single cycle's comments without rescanning the file.
    The reviewer field stays unindexed — the lead-reviewer dedup pass
    that would care about it is deferred to a later track.
    """

    def __init__(self, path: Path) -> None:
        self._collection = Collection(
            path,
            index_fields=["ticket_id", "cycle"],
        )

    async def load(self) -> None:
        await self._collection.load()

    # ---- writes ---------------------------------------------------------

    async def append(self, comment: ReviewerComment) -> str:
        """Persist ``comment`` and return its assigned id.

        The store does not enforce ``ticket_id`` non-null at persist
        time (the model allows ``None`` for legacy callers); ``for_ticket``
        / ``for_cycle`` will simply not return the row when querying by
        a specific ticket.

        Stamps ``created_at`` with the current UTC time if the incoming
        comment still carries the sentinel value. Explicit timestamps
        (e.g., backfill/replay paths) are preserved. This is the single
        write-time stamping point so every caller — MCP, dispatch,
        per_commit_runner, eval collector, sim driver — gets the same
        treatment without having to know about it.
        """
        if comment.created_at == _CREATED_AT_SENTINEL:
            comment = comment.model_copy(
                update={"created_at": datetime.now(timezone.utc)}
            )
        raw = comment.model_dump(mode="json", by_alias=True)
        return await self._collection.insert(raw)

    # ---- reads ----------------------------------------------------------

    def _load(self, raw: dict) -> ReviewerComment:
        # ``_id`` is the Collection-assigned doc id; not a model field.
        # Strip before validation so ``extra="forbid"`` doesn't trip.
        raw = {k: v for k, v in raw.items() if k != "_id"}
        return ReviewerComment.model_validate(raw)

    async def get(self, comment_id: str) -> ReviewerComment | None:
        raw = await self._collection.get(comment_id)
        return None if raw is None else self._load(raw)

    async def for_ticket(self, ticket_id: str) -> list[ReviewerComment]:
        """Every comment ever raised against ``ticket_id``, all cycles.

        **Order is non-deterministic** — this method goes through the
        set-backed index in :class:`jig.store.core.JsonlStore`, whose
        iteration order varies across processes. Use
        :meth:`for_ticket_chronological` when finding-ID stability or
        ordered output matters (story rendering, stable RC-N
        assignment, audit-trail consumers).
        """
        raws = await self._collection.find_where(ticket_id=ticket_id)
        return [self._load(r) for r in raws]

    async def for_ticket_chronological(self, ticket_id: str) -> list[ReviewerComment]:
        """Comments for ``ticket_id`` in insertion (JSONL append) order.

        Use this when the order matters — e.g., stable-ID assignment
        (``jig.finding_ids``) or chronological story rendering (``jig
        story``). The legacy ``for_ticket`` goes through a set-backed
        index whose iteration order varies across processes, so it is
        unsafe for ordered consumers.

        Implemented via ``Collection.find`` with a ticket predicate;
        ``find`` walks the in-memory ``_docs`` dict directly, which is
        insertion-ordered by Python dict semantics. The trade-off is
        full-scan filtering, but the typical per-ticket comment count
        is small (single-digit to tens), so cost is negligible relative
        to the determinism gain.
        """
        raws = await self._collection.find(lambda d: d.get("ticket_id") == ticket_id)
        return [self._load(r) for r in raws]

    async def for_cycle(self, ticket_id: str, cycle: int) -> list[ReviewerComment]:
        """Comments raised in a single review cycle for ``ticket_id``.

        The bounded fix-loop tracker uses this to compare each cycle's
        categories against the prior cycle's; same-category recurrence
        across N consecutive cycles is what trips the cap.
        """
        raws = await self._collection.find_where(ticket_id=ticket_id, cycle=cycle)
        return [self._load(r) for r in raws]


__all__ = ["ReviewCommentsStore"]
