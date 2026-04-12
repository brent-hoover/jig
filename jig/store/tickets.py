from datetime import datetime, timezone
from pathlib import Path

from jig.store.models import TypedCollection
from jig.ticket import Ticket, TicketStatus, TicketType

TOP_LEVEL_TYPES = {TicketType.FEATURE, TicketType.BUG, TicketType.CHORE}


class TicketStore:
    def __init__(self, path: Path) -> None:
        self._collection: TypedCollection[Ticket] = TypedCollection(
            path,
            model=Ticket,
            index_fields=["type", "status", "assignee", "parent_id"],
        )

    async def load(self) -> None:
        await self._collection.load()

    async def create(self, ticket: Ticket) -> str:
        return await self._collection.insert(ticket)

    async def get(self, ticket_id: str) -> Ticket | None:
        return await self._collection.get(ticket_id)

    async def update(self, ticket_id: str, **fields) -> Ticket:
        fields.setdefault("updated_at", datetime.now(timezone.utc))
        await self._collection.update(ticket_id, fields)
        loaded = await self._collection.get(ticket_id)
        assert loaded is not None
        return loaded

    async def update_status(self, ticket_id: str, status: TicketStatus) -> Ticket:
        return await self.update(ticket_id, status=status)

    async def find_in_progress_top_level(self) -> list[Ticket]:
        results: list[Ticket] = []
        for ttype in TOP_LEVEL_TYPES:
            found = await self._collection.find_where(
                type=ttype, status=TicketStatus.IN_PROGRESS
            )
            results.extend(found)
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
        for ttype in TOP_LEVEL_TYPES:
            candidates.extend(
                await self._collection.find_where(type=ttype, status=TicketStatus.OPEN)
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
