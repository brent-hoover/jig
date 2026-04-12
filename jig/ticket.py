from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import Field

from jig.store.models import StoreModel


class TicketType(str, Enum):
    FEATURE = "feature"
    BUG = "bug"
    CHORE = "chore"
    TASK = "task"
    QUESTION = "question"


class TicketStatus(str, Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    NEEDS_INFO = "needs_info"
    FAILED = "failed"
    RESOLVED = "resolved"
    CLOSED = "closed"


class Ticket(StoreModel):
    type: TicketType
    status: TicketStatus = TicketStatus.OPEN
    title: str
    description: str = ""
    assignee: str | None = None
    parent_id: str | None = None
    blocks: list[str] = []
    blocked_by: list[str] = []
    labels: list[str] = []
    created_by: str
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class Comment(StoreModel):
    ticket_id: str
    author: str
    content: str
    kind: Literal[
        "comment",
        "commit",
        "phase_run",
        "decision",
        "status_change",
        "question",
        "answer",
    ] = "comment"
    commit_sha: str | None = None
    phase_result: Literal["success", "failed", "blocked", "needs_info"] | None = None
    phase_branch: str | None = None
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
