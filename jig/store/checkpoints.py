"""CheckpointStore — append-JSONL storage for the checkpoint channel.

Mirrors ``ThreadStore``'s shape (Collection-backed, indexed on
``ticket_id`` / ``phase``) but lives in its own file
(``.jig/store/checkpoints.jsonl``) per doc 09's separation from the
thread channel.

Queries exclude ``historical=True`` records by default — phase
boundary pruning (set by ``mark_phase_historical`` on Handoff accept)
keeps the on-disk audit log intact while letting the "current view"
queries stay focused on the active phase.
"""

from __future__ import annotations

from pathlib import Path

from jig.checkpoints import Checkpoint
from jig.store.collection import Collection
from jig.thread import DeferredItem


class CheckpointStore:
    def __init__(self, path: Path) -> None:
        self._collection = Collection(
            path,
            index_fields=["ticket_id", "phase", "author"],
        )

    async def load(self) -> None:
        await self._collection.load()

    # ---- writes ---------------------------------------------------------

    async def post(self, checkpoint: Checkpoint) -> str:
        raw = checkpoint.model_dump(mode="json", by_alias=True)
        return await self._collection.insert(raw)

    async def mark_phase_historical(self, ticket_id: str, phase: str) -> int:
        """Flip every checkpoint for (ticket_id, phase) to ``historical=True``.

        Called from the Handoff-accept path so finished-phase records
        drop out of default queries but stay in the JSONL for audit.
        Returns the count of records updated.
        """
        raws = await self._collection.find_where(ticket_id=ticket_id, phase=phase)
        updated = 0
        for r in raws:
            if r.get("historical") is True:
                continue
            await self._collection.update(r["_id"], {"historical": True})
            updated += 1
        return updated

    # ---- reads ----------------------------------------------------------

    def _load(self, raw: dict) -> Checkpoint:
        return Checkpoint.model_validate(raw)

    async def get(self, checkpoint_id: str) -> Checkpoint | None:
        raw = await self._collection.get(checkpoint_id)
        return None if raw is None else self._load(raw)

    async def for_ticket(
        self, ticket_id: str, *, include_historical: bool = False
    ) -> list[Checkpoint]:
        raws = await self._collection.find_where(ticket_id=ticket_id)
        cps = [self._load(r) for r in raws]
        if not include_historical:
            cps = [c for c in cps if not c.historical]
        cps.sort(key=lambda c: c.created_at)
        return cps

    async def for_phase(
        self,
        ticket_id: str,
        phase: str,
        *,
        include_historical: bool = False,
    ) -> list[Checkpoint]:
        raws = await self._collection.find_where(ticket_id=ticket_id, phase=phase)
        cps = [self._load(r) for r in raws]
        if not include_historical:
            cps = [c for c in cps if not c.historical]
        cps.sort(key=lambda c: c.created_at)
        return cps

    async def latest(
        self,
        ticket_id: str,
        *,
        phase: str | None = None,
        include_historical: bool = False,
    ) -> Checkpoint | None:
        """Most recent checkpoint for a ticket (optionally phase-scoped)."""
        cps = (
            await self.for_phase(
                ticket_id, phase, include_historical=include_historical
            )
            if phase is not None
            else await self.for_ticket(ticket_id, include_historical=include_historical)
        )
        return cps[-1] if cps else None

    async def deferred_items_open(
        self, ticket_id: str, phase: str
    ) -> list[DeferredItem]:
        """Open deferred items accumulated on a phase's checkpoints.

        Task F's ``thread_handoff`` reads these and packages them into
        the Handoff entry. "Open" means ``status == "open"`` — done /
        promoted / accepted items aren't surfaced to the evaluator
        again.
        """
        cps = await self.for_phase(ticket_id, phase)
        items: list[DeferredItem] = []
        for cp in cps:
            for d in cp.deferred:
                if d.status == "open":
                    items.append(d)
        return items


__all__ = [
    "CheckpointStore",
]
