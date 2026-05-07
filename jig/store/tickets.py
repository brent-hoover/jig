import asyncio
import inspect
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Union

from jig.store.models import TypedCollection
from jig.ticket import Ticket, TicketStatus, WorkType

StatusChangeCallback = Callable[[str, Union[str, None], str], Union[Awaitable[None], None]]

# All shipped work_types currently participate in the workflow pipeline.
# Phase 2 will introduce per-work_type workflow selection via config.yaml,
# but for now every top-level ticket is eligible.
TOP_LEVEL_WORK_TYPES = set(WorkType)

# Backwards-compat alias used by older imports — remove with TicketType.
TOP_LEVEL_TYPES = TOP_LEVEL_WORK_TYPES


class TicketStore:
    def __init__(self, path: Path) -> None:
        self._collection: TypedCollection[Ticket] = TypedCollection(
            path,
            model=Ticket,
            index_fields=["work_type", "status", "assignee", "parent_id"],
        )
        self._on_status_change: StatusChangeCallback | None = None

    def set_status_change_callback(self, cb: StatusChangeCallback | None) -> None:
        """Register a callback fired on every observed status transition.

        Used by the orchestrator to feed ``TicketStateChanged`` analytics
        events without coupling the store to the analytics schema. The
        callback receives ``(ticket_id, from_state, to_state)``; sync or
        async returns are both supported. ``from_state`` is None for the
        first observed status (initial creation paths).
        """
        self._on_status_change = cb

    async def load(self) -> None:
        await self._collection.load()

    async def create(self, ticket: Ticket) -> str:
        # Uniqueness is enforced inside Collection.insert under its
        # asyncio.Lock — no TOCTOU window between check and append.
        # We re-raise with a ticket-specific message so callers (CLI,
        # init flow) get a domain-friendly error.
        try:
            return await self._collection.insert(ticket)
        except ValueError as e:
            if "already exists" in str(e):
                raise ValueError(
                    f"ticket with id {ticket.id!r} already exists"
                ) from e
            raise

    async def get(self, ticket_id: str) -> Ticket | None:
        return await self._collection.get(ticket_id)

    async def update(self, ticket_id: str, **fields) -> Ticket:
        prev_status: str | None = None
        if self._on_status_change is not None and "status" in fields:
            prev = await self._collection.get(ticket_id)
            prev_status = prev.status.value if prev is not None else None
        fields.setdefault("updated_at", datetime.now(timezone.utc))
        await self._collection.update(ticket_id, fields)
        loaded = await self._collection.get(ticket_id)
        assert loaded is not None
        if self._on_status_change is not None and "status" in fields:
            new_status = loaded.status.value
            if new_status != prev_status:
                self._fire_status_change(ticket_id, prev_status, new_status)
        return loaded

    def _fire_status_change(
        self, ticket_id: str, from_state: str | None, to_state: str
    ) -> None:
        cb = self._on_status_change
        if cb is None:
            return
        result = cb(ticket_id, from_state, to_state)
        if inspect.isawaitable(result):
            asyncio.create_task(result)  # type: ignore[arg-type]

    async def update_status(self, ticket_id: str, status: TicketStatus) -> Ticket:
        return await self.update(ticket_id, status=status)

    async def find_in_progress_top_level(self) -> list[Ticket]:
        results: list[Ticket] = []
        for wt in TOP_LEVEL_WORK_TYPES:
            for status in (TicketStatus.IN_PROGRESS, TicketStatus.NEEDS_INFO):
                found = await self._collection.find_where(
                    work_type=wt, status=status
                )
                results.extend(t for t in found if t.workflow != "thread")
        return results

    async def find_by_assignee(self, assignee: str) -> list[Ticket]:
        return await self._collection.find_where(assignee=assignee)

    async def find_by_parent(self, parent_id: str) -> list[Ticket]:
        return await self._collection.find_where(parent_id=parent_id)

    async def list_all(self) -> list[Ticket]:
        return await self._collection.find()

    async def find_ready(self) -> list[Ticket]:
        """Find open top-level tickets whose dependencies are all resolved."""
        candidates: list[Ticket] = []
        for wt in TOP_LEVEL_WORK_TYPES:
            candidates.extend(
                t
                for t in await self._collection.find_where(
                    work_type=wt, status=TicketStatus.OPEN
                )
                if t.workflow != "thread"
            )
        ready: list[Ticket] = []
        for ticket in candidates:
            if not ticket.blocked_by:
                ready.append(ticket)
                continue
            all_resolved = True
            for dep_id in ticket.blocked_by:
                dep = await self.get(dep_id)
                if dep is None or dep.status != TicketStatus.RESOLVED:
                    all_resolved = False
                    break
            if all_resolved:
                ready.append(ticket)
        return ready
