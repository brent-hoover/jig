from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import Field, field_validator, model_validator

from jig.safe_path import validate_safe_path_segment
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
    BRIEF = "brief"
    ARCHITECTURE = "architecture"


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
    # All phases completed successfully, but the branch couldn't
    # auto-merge into the default branch — the worktree/branch are
    # preserved and the ticket sits in this state until a human
    # resolves the conflict. Distinct from RESOLVED (merged cleanly)
    # and FAILED (a phase itself failed).
    MERGE_CONFLICT = "merge_conflict"
    RESOLVED = "resolved"
    CLOSED = "closed"


class Ticket(StoreModel):
    work_type: WorkType
    size: Size = Size.M
    status: TicketStatus = TicketStatus.OPEN
    title: str
    description: str = ""
    assignee: str | None = None
    derived_from: str | None = None  # e.g. "project://spec/capabilities/due-dates"
    parent_id: str | None = None
    blocks: list[str] = []
    blocked_by: list[str] = []
    workflow: str = "default"
    labels: list[str] = []
    created_by: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # v2 build-plan extensions. Optional during the v2 build so v1 records
    # still load; populated by the Planner PM when the build plan owns the
    # ticket. See docs/pm-workflow/design.md §"Ticket structure (extensions)".
    suite_id: str | None = None
    module_id: str | None = None
    capability_ids: list[str] = []
    epic_id: str | None = None
    layer: str | None = None  # bones | mvp | final
    dev_tier: str | None = None  # standard | senior | sa
    reviewer_set: list[str] = []
    context_hints: dict[str, Any] = {}
    risks_addressed: list[str] = []
    done_when: str | None = None

    # v2 Track D MVP — VD wireframes referenced by this ticket. Each
    # entry is a screen-id matching a ``.jig/spec/wireframes/<id>.html``
    # file. The visual_compliance reviewer walks this list to verify
    # the wireframe exists, lints clean, and is referenced in the dev's
    # diff. Empty list means "this ticket implements no UI" — the
    # reviewer is skipped per dispatch logic.
    visual_references: list[str] = []

    # Set when the Coordinator defers this ticket via the DEFERRED queue
    # (per docs/pm-workflow/design.md §"DEFERRED queue triage"). The
    # ticket itself stays in the store; this stamp lets downstream views
    # filter "currently deferred" without needing to consult the queue.
    deferred_at: datetime | None = None

    # v2 Track G Final — set when this ticket amends an existing
    # behavioral / data contract as part of its work (per
    # ``docs/pm-workflow/design.md`` §"Reviewer federation — selection
    # logic"). When populated, the dispatch logic auto-selects
    # ``reviewer-architectural`` so the SA-tier reviewer can verify
    # the amendment was deliberate. The string is a short rationale
    # (e.g. ``"adds optional retry-policy field to ingest-batch-atomicity"``);
    # the SA reviewer reads it for context. ``None`` means "no contract
    # amendment in this ticket".
    contract_amendment: str | None = None

    @field_validator("id")
    @classmethod
    def _validate_id_is_path_safe(cls, value: str) -> str:
        """Ticket ids land in worktree paths and git refs.

        ``jig/worktree.py`` builds ``.jig/worktrees/<ticket_id>`` and
        the branch name ``jig/<ticket_id>`` directly from this field.
        A malicious or malformed id (``../etc``, ``with space``,
        ``-flag``) could escape the worktree root or produce a
        dangerous git ref. Validate at construction so every Ticket
        instance is safe by the time it reaches a path helper —
        defense in depth complementing the per-helper validation.
        """
        return validate_safe_path_segment(value, "Ticket.id")

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
