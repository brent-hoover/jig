from datetime import datetime, timezone
from pathlib import Path

from pydantic import Field

from jig.store.models import StoreModel, TypedCollection


class Handoff(StoreModel):
    issue_id: str
    from_phase: str
    to_phase: str
    summary: str
    artifacts: list[str] = Field(default_factory=list)
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class Learning(StoreModel):
    issue_id: str
    phase: str
    content: str
    tags: list[str] = Field(default_factory=list)
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class MemoryStore:
    def __init__(self, path: Path) -> None:
        self._handoffs: TypedCollection[Handoff] = TypedCollection(
            path / "handoffs.jsonl",
            model=Handoff,
            index_fields=["issue_id", "to_phase"],
        )
        self._learnings: TypedCollection[Learning] = TypedCollection(
            path / "learnings.jsonl",
            model=Learning,
            index_fields=["issue_id"],
        )

    async def load(self) -> None:
        await self._handoffs.load()
        await self._learnings.load()

    async def write_handoff(
        self,
        issue_id: str,
        from_phase: str,
        to_phase: str,
        summary: str,
        artifacts: list[str] | None = None,
    ) -> str:
        handoff = Handoff(
            issue_id=issue_id,
            from_phase=from_phase,
            to_phase=to_phase,
            summary=summary,
            artifacts=artifacts or [],
        )
        return await self._handoffs.insert(handoff)

    async def read_handoff(
        self, issue_id: str, to_phase: str
    ) -> Handoff | None:
        results = await self._handoffs.find_where(
            issue_id=issue_id, to_phase=to_phase
        )
        if not results:
            return None
        results.sort(key=lambda h: h.timestamp, reverse=True)
        return results[0]

    async def add_learning(
        self,
        issue_id: str,
        phase: str,
        content: str,
        tags: list[str] | None = None,
    ) -> str:
        learning = Learning(
            issue_id=issue_id,
            phase=phase,
            content=content,
            tags=tags or [],
        )
        return await self._learnings.insert(learning)

    async def get_learnings(
        self,
        issue_id: str,
        tags: list[str] | None = None,
        limit: int = 10,
    ) -> list[Learning]:
        results = await self._learnings.find_where(issue_id=issue_id)
        if tags:
            tag_set = set(tags)
            results = [
                l for l in results
                if tag_set.intersection(l.tags)
            ]
        results.sort(key=lambda l: l.timestamp, reverse=True)
        return results[:limit]

    async def get_context_block(
        self, issue_id: str, to_phase: str
    ) -> str:
        handoff = await self.read_handoff(issue_id, to_phase)
        learnings = await self.get_learnings(issue_id, limit=10)

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
                    f" (tags: {', '.join(learning.tags)})"
                    if learning.tags else ""
                )
                parts.append(f"- {learning.content}{tag_suffix}")
        return "\n".join(parts)
