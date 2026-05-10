"""CanonicalizationIssueStore — JSONL storage for tracked canonicalization issues."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import Field

from jig.store.collection import Collection
from jig.store.models import StoreModel


IssueType = Literal[
    "convention_violation",
    "structural_violation",
    "rule_conflict",
]
IssueRouting = Literal["human_review", "agent_resolution"]
IssueStatus = Literal["open", "resolved", "wontfix"]


class CanonicalizationIssue(StoreModel):
    """A canonicalization rule match that requires follow-up."""

    type: IssueType
    rule_id: str
    rule_message: str
    diff_hunk: str
    file_path: str
    ticket_id: str = ""
    work_unit_refs: list[str] = Field(default_factory=list)
    suggested_fixes: list[str] = Field(default_factory=list)
    routing: IssueRouting = "human_review"
    status: IssueStatus = "open"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class CanonicalizationIssueStore:
    """Append-only JSONL store for ``CanonicalizationIssue`` records."""

    def __init__(self, path: Path) -> None:
        self._collection = Collection(
            path,
            index_fields=["ticket_id", "status"],
        )

    async def load(self) -> None:
        await self._collection.load()

    async def append(self, issue: CanonicalizationIssue) -> str:
        raw = issue.model_dump(mode="json", by_alias=True)
        return await self._collection.insert(raw)

    def _load(self, raw: dict) -> CanonicalizationIssue:
        return CanonicalizationIssue.model_validate(raw)

    async def for_ticket(self, ticket_id: str) -> list[CanonicalizationIssue]:
        raws = await self._collection.find_where(ticket_id=ticket_id)
        return [self._load(r) for r in raws]

    async def open_issues(self) -> list[CanonicalizationIssue]:
        raws = await self._collection.find_where(status="open")
        return [self._load(r) for r in raws]

    async def resolve(self, issue_id: str) -> bool:
        return await self._collection.update(issue_id, {"status": "resolved"})

    async def wontfix(self, issue_id: str) -> bool:
        return await self._collection.update(issue_id, {"status": "wontfix"})


__all__ = [
    "CanonicalizationIssue",
    "CanonicalizationIssueStore",
    "IssueRouting",
    "IssueStatus",
    "IssueType",
]
