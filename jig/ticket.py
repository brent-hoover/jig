from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import Field, model_validator

from jig.store.models import StoreModel


class WorkType(str, Enum):
    """Classification axis: what kind of work this ticket represents.

    Per docs/03-specs-and-work-types.md. The shipped set is intentionally
    small and opinionated.
    """

    FEATURE = "feature"
    BUGFIX = "bugfix"
    REFACTOR = "refactor"
    SPIKE = "spike"
    PERF = "perf"
    MIGRATION = "migration"
    DOCS = "docs"


# Transitional alias. Remove in the next release cycle once the doc rename
# propagates to any clients that used the old name.
TicketType = WorkType


class Size(str, Enum):
    """Classification axis: rough cost/risk of the ticket.

    Calibration is team-specific (see docs/03-specs-and-work-types.md).
    """

    XS = "xs"
    S = "s"
    M = "m"
    L = "l"
    XL = "xl"


# Mapping from the pre-doc-03 `type` values to the new `work_type`.
# See docs/implementation-plan.md Phase 1 Task B.
_LEGACY_TYPE_MIGRATION: dict[str, str] = {
    "feature": "feature",
    "bug": "bugfix",
    "chore": "refactor",
    "task": "refactor",
    # "question" is no longer a work_type; per doc 08 questions will be
    # thread entries (Phase 4). For now we migrate to "feature" so old
    # records still load; manual re-tagging is expected.
    "question": "feature",
}


class TicketStatus(str, Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    NEEDS_INFO = "needs_info"
    FAILED = "failed"
    RESOLVED = "resolved"
    CLOSED = "closed"


class Ticket(StoreModel):
    work_type: WorkType
    size: Size = Size.M
    status: TicketStatus = TicketStatus.OPEN
    title: str
    description: str = ""
    assignee: str | None = None
    parent_id: str | None = None
    blocks: list[str] = []
    blocked_by: list[str] = []
    workflow: str = "default"
    labels: list[str] = []
    created_by: str
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_fields(cls, data: Any) -> Any:
        """Accept pre-doc-03 ticket records.

        - Old field `type` → `work_type`, with value migration.
        - Missing `size` defaults to `m`.
        - Legacy `task`/`question` types were always thread-style
          (routed to the dispatch loop, not the workflow pipeline).
          Preserve that by defaulting `workflow="thread"` when the
          caller didn't specify one. Phase 4 replaces this with proper
          typed thread entries on their parent ticket.

        This runs on every Ticket(...) construction, so it also lets
        callers pass `type=` as a kwarg during the transition.
        """
        if not isinstance(data, dict):
            return data
        if "work_type" not in data and "type" in data:
            legacy = data.pop("type")
            if isinstance(legacy, WorkType):
                data["work_type"] = legacy
            else:
                data["work_type"] = _LEGACY_TYPE_MIGRATION.get(legacy, legacy)
                if legacy in ("task", "question") and "workflow" not in data:
                    data["workflow"] = "thread"
        return data


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
        "proposal",  # Phase 3E — first typed thread entry per doc 04/08.
    ] = "comment"
    commit_sha: str | None = None
    phase_result: Literal["success", "failed", "blocked", "needs_info"] | None = None
    phase_branch: str | None = None
    # Proposal-specific payload (only set when kind="proposal" or when
    # recording an accept/reject/refine that references one). Phase 4
    # will replace this envelope with properly typed thread entries;
    # for now, extending Comment keeps migrations small.
    proposal_target: str | None = None  # e.g., "ticket://spec.behaviors"
    proposal_section: str | None = None  # optional sub-section
    proposal_change: str | None = None  # YAML fragment or prose change
    proposal_state: Literal[
        "pending", "accepted", "rejected", "refining"
    ] | None = None
    proposal_parent_id: str | None = None  # resolver entries reference original
    proposal_owners: list[str] = []  # routing result cached at creation
    # Spec versioning: when an accepted proposal applies to a ticket
    # spec, record the new version number so the audit trail is clear.
    proposal_spec_version: int | None = None
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
