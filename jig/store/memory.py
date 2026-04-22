from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import Field, model_validator

from jig.store.models import StoreModel, TypedCollection


def _migrate_issue_id(data: Any) -> Any:
    """Accept pre-rename records that used ``issue_id`` as the FK.

    JSONL stores are append-only, so historic entries written before
    the issue→ticket rename still carry ``issue_id``. Silently aliasing
    it to ``ticket_id`` at construction time lets old files load
    without a migration pass. Drop this shim in a later release once
    all stores have been rewritten.
    """
    if not isinstance(data, dict):
        return data
    if "ticket_id" not in data and "issue_id" in data:
        data["ticket_id"] = data.pop("issue_id")
    return data


class Handoff(StoreModel):
    ticket_id: str
    from_phase: str
    to_phase: str
    summary: str
    artifacts: list[str] = Field(default_factory=list)
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    _migrate_issue_id = model_validator(mode="before")(_migrate_issue_id)


class Learning(StoreModel):
    ticket_id: str
    phase: str
    content: str
    role: str = ""
    tags: list[str] = Field(default_factory=list)
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    _migrate_issue_id = model_validator(mode="before")(_migrate_issue_id)


class MemoryStore:
    def __init__(self, path: Path) -> None:
        self._handoffs: TypedCollection[Handoff] = TypedCollection(
            path / "handoffs.jsonl",
            model=Handoff,
            index_fields=["ticket_id", "to_phase"],
        )
        self._learnings: TypedCollection[Learning] = TypedCollection(
            path / "learnings.jsonl",
            model=Learning,
            index_fields=["ticket_id", "role"],
        )

    async def load(self) -> None:
        await self._handoffs.load()
        await self._learnings.load()

    async def write_handoff(
        self,
        ticket_id: str,
        from_phase: str,
        to_phase: str,
        summary: str,
        artifacts: list[str] | None = None,
    ) -> str:
        handoff = Handoff(
            ticket_id=ticket_id,
            from_phase=from_phase,
            to_phase=to_phase,
            summary=summary,
            artifacts=artifacts or [],
        )
        return await self._handoffs.insert(handoff)

    async def read_handoff(self, ticket_id: str, to_phase: str) -> Handoff | None:
        results = await self._handoffs.find_where(
            ticket_id=ticket_id, to_phase=to_phase
        )
        if not results:
            return None
        results.sort(key=lambda h: h.timestamp, reverse=True)
        return results[0]

    async def add_learning(
        self,
        ticket_id: str,
        phase: str,
        content: str,
        tags: list[str] | None = None,
    ) -> str:
        learning = Learning(
            ticket_id=ticket_id,
            phase=phase,
            content=content,
            tags=tags or [],
        )
        return await self._learnings.insert(learning)

    async def get_learnings(
        self,
        ticket_id: str,
        tags: list[str] | None = None,
        limit: int = 10,
    ) -> list[Learning]:
        results = await self._learnings.find_where(ticket_id=ticket_id)
        if tags:
            tag_set = set(tags)
            results = [item for item in results if tag_set.intersection(item.tags)]
        results.sort(key=lambda item: item.timestamp, reverse=True)
        return results[:limit]

    async def get_context_block(self, ticket_id: str, to_phase: str) -> str:
        handoff = await self.read_handoff(ticket_id, to_phase)
        learnings = await self.get_learnings(ticket_id, limit=10)

        parts: list[str] = []
        if handoff is not None:
            parts.append(f"## Handoff from {handoff.from_phase}")
            parts.append(handoff.summary)
        if learnings:
            if parts:
                parts.append("")  # blank line
            parts.append("## Learnings")
            for learning in learnings:
                tag_suffix = (
                    f" (tags: {', '.join(learning.tags)})" if learning.tags else ""
                )
                parts.append(f"- {learning.content}{tag_suffix}")
        return "\n".join(parts)

    async def add_role_learning(self, *, role: str, content: str) -> str:
        learning = Learning(
            ticket_id="",
            phase="",
            content=content,
            role=role,
        )
        return await self._learnings.insert(learning)

    async def get_role_learnings(self, role: str, limit: int = 20) -> list[Learning]:
        results = await self._learnings.find_where(role=role)
        results.sort(key=lambda learning: learning.timestamp)
        return results[:limit]
