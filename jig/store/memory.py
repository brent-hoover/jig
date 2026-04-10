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
