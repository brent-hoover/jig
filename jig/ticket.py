from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import Field, field_validator, model_validator

from jig.safe_path import validate_safe_path_segment
from jig.schemas._validators import validate_kebab_id, validate_tz_aware
from jig.store.models import StoreModel

# v2 build-plan fields carry a small literal vocabulary the schemas team
# explicitly enumerates (see ``docs/pm-workflow/design.md`` §"Layer model"
# and §"Dev tier"). We keep the allowed sets as module-level frozensets
# so the validator and downstream consumers can share one source of truth.
_ALLOWED_LAYERS = frozenset({"bones", "mvp", "final"})
_ALLOWED_DEV_TIERS = frozenset({"standard", "senior", "sa"})


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

    # ---- v2 schema discipline (Block 4) -----------------------------------
    #
    # The shared ``jig/schemas/*`` v2 models all use ``validate_kebab_id`` /
    # ``validate_tz_aware`` for ids and timestamps. The pre-v2 ``Ticket``
    # model carries the most-touched v2 fields (``suite_id``, ``module_id``,
    # ``epic_id``, etc.) but had no equivalent guards — any operator-edited
    # YAML or older fixture could leak ``"Catalog Ingest"`` / naive
    # datetimes into worktree paths, ticket-store reads, and reviewer
    # federation paths. These validators bring Ticket onto the same
    # invariants the schemas package already enforces.

    @field_validator("created_at", "updated_at")
    @classmethod
    def _tz_required_timestamps(cls, v: datetime) -> datetime:
        """``created_at``/``updated_at`` are required + always present;
        Pydantic feeds the validator the resolved value (default factories
        always emit tz-aware UTC), so ``None`` never reaches here."""
        return validate_tz_aware(v, "Ticket.<timestamp>")

    @field_validator("deferred_at")
    @classmethod
    def _tz_optional_deferred_at(cls, v: datetime | None) -> datetime | None:
        """``deferred_at`` is optional — only enforce tz on present values."""
        return validate_tz_aware(v, "Ticket.deferred_at") if v is not None else v

    @field_validator("suite_id", "module_id", "epic_id")
    @classmethod
    def _kebab_optional_ids(cls, v: str | None) -> str | None:
        """v2 build-plan extension ids must be kebab-case when populated.

        These ids round-trip through PM artifacts (``build-plan.yaml``),
        the worktree's spec compliance reviewer, and the federation
        reviewer dispatch. Keeping them on the same kebab grammar as the
        ``jig/schemas/*`` ids stops mismatches at the boundary instead of
        deep in a reviewer's path lookup.
        """
        if v is None:
            return v
        # Field name is whichever caller triggered the validator; the
        # validator can introspect it via ``ValidationInfo`` but the
        # ergonomic win there isn't worth the extra arg here — the error
        # message includes the value, which is enough to triage.
        return validate_kebab_id(v, "Ticket.<id-field>")

    @field_validator("capability_ids", "visual_references")
    @classmethod
    def _kebab_id_lists(cls, v: list[str]) -> list[str]:
        """Each entry in these lists is a kebab-id used as a path segment.

        ``capability_ids`` are the L1-discovery capability ids the spec-
        compliance reviewer cross-references; ``visual_references`` are
        screen-id-derived paths the visual reviewer reads from
        ``.jig/spec/wireframes/<id>.html``. Bad ids here turn into
        path-traversal-shaped lookups inside the reviewer.
        """
        for entry in v:
            validate_kebab_id(entry, "Ticket.<id-list>[]")
        return v

    @field_validator("layer")
    @classmethod
    def _allowed_layer(cls, v: str | None) -> str | None:
        """``layer`` ∈ {``bones``, ``mvp``, ``final``} when present.

        Kept as ``str | None`` rather than a Literal because the field is
        optional in v1 records and we still want the validator's error
        message to show the allowed values explicitly (Pydantic's default
        Literal error mentions the field, not the universe).
        """
        if v is None:
            return v
        if v not in _ALLOWED_LAYERS:
            raise ValueError(
                f"Ticket.layer must be one of {sorted(_ALLOWED_LAYERS)!r}, "
                f"got {v!r}"
            )
        return v

    @field_validator("dev_tier")
    @classmethod
    def _allowed_dev_tier(cls, v: str | None) -> str | None:
        """``dev_tier`` ∈ {``standard``, ``senior``, ``sa``} when present.

        Mirrors ``arch.TierHint`` exactly — the SA's ``tier_hint`` is
        the upstream source for this value via the Planner's tier
        promotion logic.
        """
        if v is None:
            return v
        if v not in _ALLOWED_DEV_TIERS:
            raise ValueError(
                f"Ticket.dev_tier must be one of "
                f"{sorted(_ALLOWED_DEV_TIERS)!r}, got {v!r}"
            )
        return v

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
