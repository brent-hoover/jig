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

    async def find_deferred_item(
        self, ticket_id: str, item_id: str
    ) -> tuple[str, DeferredItem] | None:
        """Locate an embedded ``DeferredItem`` by id within a ticket.

        Returns ``(checkpoint_id, item)`` or ``None``. Searches
        historical checkpoints too so a promote call that arrives
        after ``mark_phase_historical`` (e.g., during handoff-accept
        review) still finds the authoring record.

        Phase 5 Task J uses this to resolve ``deferred_item_id`` to
        the owning checkpoint before mutating the item's status.
        """
        cps = await self.for_ticket(ticket_id, include_historical=True)
        for cp in cps:
            for d in cp.deferred:
                if d.id == item_id:
                    return (cp.id, d)
        return None

    async def update_deferred_item(
        self,
        checkpoint_id: str,
        item_id: str,
        *,
        status: str | None = None,
        promoted_ticket_id: str | None = None,
    ) -> bool:
        """Update a single embedded ``DeferredItem`` in place.

        Returns ``True`` when the item was found and the checkpoint
        was rewritten; ``False`` if the checkpoint or item is
        missing.

        Phase 5 Task J uses this to mark items ``promoted`` and
        record the new child ticket id without touching other
        fields on the checkpoint record.
        """
        raw = await self._collection.get(checkpoint_id)
        if raw is None:
            return False
        items = list(raw.get("deferred", []))
        found = False
        for d in items:
            if d.get("id") == item_id:
                if status is not None:
                    d["status"] = status
                if promoted_ticket_id is not None:
                    d["promoted_ticket_id"] = promoted_ticket_id
                found = True
                break
        if not found:
            return False
        await self._collection.update(checkpoint_id, {"deferred": items})
        return True


__all__ = [
    "CheckpointStore",
]
