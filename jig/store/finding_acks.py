"""FindingAcksStore — append-JSONL audit trail for reviewer findings.

Sibling of :class:`jig.store.review_comments.ReviewCommentsStore`.
Records the per-finding acks that close the fix-loop audit trail:

- ``addressed`` — dev (or any back-routed phase agent) called
  ``mark_finding_addressed`` claiming the finding is resolved by a
  specific change.
- ``resolved`` — judgment reviewer called ``mark_finding_resolved``
  confirming the dev's fix actually closed the issue.
- ``reraised`` — orchestrator-generated record produced when the next
  federation pass flags the same signature again, indicating the dev's
  claim did not hold. Captures the new comment's prose so the audit
  trail is self-contained.
- ``dismissed`` — SA adjudication verdict: the finding is wrong or not
  worth blocking. Binding — the federation gate filters blocking
  comments whose ``persistence_key`` carries a dismissal, in all later
  cycles (review-severity-binary §5).

The store does not assign or own finding IDs — that's
``jig.finding_ids.compute_finding_ids``, a pure function over
chronologically-ordered ``ReviewerComment`` rows. The ack store just
records what was claimed against which ID, by whom, in which cycle.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from jig.store.collection import Collection

_CREATED_AT_SENTINEL = datetime(1, 1, 1, tzinfo=timezone.utc)


class FindingAck(BaseModel):
    """One ack event recorded against a reviewer finding."""

    model_config = ConfigDict(extra="forbid")

    ticket_id: str = Field(..., min_length=1)
    finding_id: str = Field(
        ...,
        min_length=1,
        description=(
            "The stable per-ticket finding identifier (e.g. ``RC-3``) "
            "produced by ``jig.finding_ids.compute_finding_ids``."
        ),
    )
    kind: Literal["addressed", "resolved", "reraised", "reject", "dismissed"]
    author: str = Field(
        ...,
        min_length=1,
        description=(
            "Role name of the agent that produced this ack — e.g. "
            "``dev``, ``reviewer-pattern-conformance``, "
            "``orchestrator`` (for auto-reraised acks)."
        ),
    )
    cycle: int = Field(default=0, ge=0)
    prose: str = Field(
        ...,
        description=(
            "Short explanation. For ``addressed``: how the dev claims "
            'they resolved it ("removed _HN_BASE constant"). For '
            "``resolved``: the reviewer's confirmation (\"confirmed; "
            'single BASE definition"). For ``reraised``: the new '
            "finding's prose so the audit trail is self-contained."
        ),
    )
    persistence_key: str | None = Field(
        default=None,
        description=(
            "Coarse cross-round key (``reviewer|type|file``) recorded on "
            "``dismissed`` acks so the binding-dismissal gate filter "
            "survives line drift and daemon restarts. None for other kinds."
        ),
    )
    created_at: datetime = Field(default=_CREATED_AT_SENTINEL)

    @field_validator("created_at", mode="after")
    @classmethod
    def _ensure_aware_created_at(cls, v: datetime) -> datetime:
        """Normalize naive datetimes to UTC-aware so audit-trail
        sorting can't crash on aware/naive comparison."""
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v


class FindingAcksStore:
    """Append-only JSONL store for ``FindingAck`` records.

    Indexed on ``ticket_id`` and ``finding_id`` so the orchestrator
    can cheaply gather acks for one finding without scanning the full
    log. Stamps ``created_at`` at append time the same way
    ``ReviewCommentsStore`` does — single write-path stamping covers
    every caller.
    """

    def __init__(self, path: Path) -> None:
        self._collection = Collection(
            path,
            index_fields=["ticket_id", "finding_id"],
        )

    async def load(self) -> None:
        await self._collection.load()

    # ---- writes ---------------------------------------------------------

    async def append(self, ack: FindingAck) -> str:
        if ack.created_at == _CREATED_AT_SENTINEL:
            ack = ack.model_copy(update={"created_at": datetime.now(timezone.utc)})
        raw = ack.model_dump(mode="json", by_alias=True)
        return await self._collection.insert(raw)

    # ---- reads ----------------------------------------------------------

    def _load(self, raw: dict) -> FindingAck:
        raw = {k: v for k, v in raw.items() if k != "_id"}
        return FindingAck.model_validate(raw)

    async def get(self, ack_id: str) -> FindingAck | None:
        raw = await self._collection.get(ack_id)
        return None if raw is None else self._load(raw)

    async def for_ticket(self, ticket_id: str) -> list[FindingAck]:
        """Acks for ``ticket_id`` in insertion (append) order.

        Goes through ``Collection.find`` (which walks the in-memory
        ``_docs`` dict — insertion-ordered) rather than
        ``find_where``, which uses set-backed indexes whose iteration
        order varies across processes. Audit-trail consumers
        (status calculation, story interleaving) rely on this order.
        """
        raws = await self._collection.find(lambda d: d.get("ticket_id") == ticket_id)
        return [self._load(r) for r in raws]

    async def for_finding(self, ticket_id: str, finding_id: str) -> list[FindingAck]:
        """Acks for one finding in append order. See ``for_ticket``."""
        raws = await self._collection.find(
            lambda d: (
                d.get("ticket_id") == ticket_id and d.get("finding_id") == finding_id
            )
        )
        return [self._load(r) for r in raws]

    async def find_id_for(
        self,
        *,
        ticket_id: str,
        finding_id: str,
        kind: str,
        author: str,
        cycle: int,
    ) -> str | None:
        """Return the row id of an existing ack matching all five fields,
        or ``None`` when none exists. Single in-memory walk; the idempotency
        check in the MCP handler uses this rather than reaching into
        ``_collection`` directly.
        """
        raws = await self._collection.find(
            lambda d: (
                d.get("ticket_id") == ticket_id
                and d.get("finding_id") == finding_id
                and d.get("kind") == kind
                and d.get("author") == author
                and d.get("cycle") == cycle
            )
        )
        if not raws:
            return None
        return str(raws[0]["_id"])


__all__ = ["FindingAck", "FindingAcksStore"]
