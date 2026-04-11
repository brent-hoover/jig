from pathlib import Path

from jig.store.models import TypedCollection
from jig.ticket import Comment


class CommentStore:
    def __init__(self, path: Path) -> None:
        self._collection: TypedCollection[Comment] = TypedCollection(
            path,
            model=Comment,
            index_fields=["ticket_id", "kind", "author"],
        )

    async def load(self) -> None:
        await self._collection.load()

    async def post(self, comment: Comment) -> str:
        return await self._collection.insert(comment)

    async def for_ticket(self, ticket_id: str) -> list[Comment]:
        found = await self._collection.find_where(ticket_id=ticket_id)
        found.sort(key=lambda c: c.created_at)
        return found

    async def phase_runs_for(self, ticket_id: str) -> list[Comment]:
        all_for = await self.for_ticket(ticket_id)
        return [c for c in all_for if c.kind == "phase_run"]

    async def commits_for(self, ticket_id: str) -> list[Comment]:
        all_for = await self.for_ticket(ticket_id)
        return [c for c in all_for if c.kind == "commit"]
