import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from jig.safe_path import validate_safe_path_segment
from jig.schemas._validators import validate_kebab_id, validate_tz_aware
from jig.store.models import StoreModel

# v2 build-plan fields carry a small literal vocabulary the schemas team
# explicitly enumerates (see ``docs/v2.0/pm-workflow/design.md`` §"Layer model"
# and §"Dev tier"). We keep the allowed sets as module-level frozensets
# so the validator and downstream consumers can share one source of truth.
_ALLOWED_LAYERS = frozenset({"bones", "mvp", "final"})
_ALLOWED_DEV_TIERS = frozenset({"standard", "senior", "sa"})

# Work-type values that must carry an Acceptance Criteria section in
# ``description``. System work types (BRIEF, ARCHITECTURE, PLANNING,
# CANONICALIZE, DOCS) orchestrate other work and don't carry their own
# AC, so they're exempt. Kept as a string set so the validator can run
# before the WorkType enum has fully resolved (the enum is defined
# later in this module, but the model_validator only inspects the
# already-coerced value).
_WORK_TYPES_REQUIRING_AC: frozenset[str] = frozenset(
    {
        "feature",
        "bugfix",
        "refactor",
        "spike",
        "perf",
        "migration",
    }
)

# Recognized AC section labels. The reviewer-test-adequacy role's prompt
# enumerates the same vocabulary; keep them in sync if you broaden one.
# Case-insensitive match; the regex below also tolerates a trailing
# ``:`` and optional surrounding whitespace.
_AC_LABELS: tuple[str, ...] = (
    "acceptance criteria",
    "acceptance",
    "acs",
    "done when",
    "done-when",
)

# Matches an AC-section heading at the start of a line. Captures any of
# the recognized labels in any of the supported formats:
#   - H2 / H3 markdown headings: ``## label`` / ``### label``
#   - Bold inline label: ``**label**`` or ``**label:**``
# The label match is case-insensitive; a trailing ``:`` (with or without
# surrounding whitespace) is tolerated; the rest of the heading line is
# ignored. The match ends at the end of the heading line so the bullet
# scan below can resume from there.
_AC_HEADING_RE: re.Pattern[str] = re.compile(
    r"(?im)^[ \t]*(?:"
    r"\#{2,3}[ \t]+(?:" + "|".join(_AC_LABELS) + r")[ \t]*:?[ \t]*"
    r"|"
    r"\*\*(?:" + "|".join(_AC_LABELS) + r")[ \t]*:?\*\*[ \t]*:?[ \t]*"
    r")$"
)

# A bullet under an AC section: ``- text`` / ``* text`` / ``N. text``.
# The leading whitespace is tolerated (some authors indent). Blank lines
# and interleaved prose between the heading and the first bullet are
# also allowed — the walker handles them implicitly by falling through
# to the "stay in AC scope" path until a real heading boundary closes
# the section.
_BULLET_RE: re.Pattern[str] = re.compile(r"^[ \t]*(?:[-*]|\d+\.)[ \t]+\S")
# Section-terminating heading detection. Matches:
#   - ``# `` … ``###### `` (H1-H6 markdown heading)
#   - ``**label**`` / ``**label:**`` *consuming the whole line* — a
#     heading-style bold label, the same shape the PO role uses.
#
# Critically, this does NOT match bold emphasis like
# ``**Important:** the widget must load in 200ms`` where the bold
# token is followed by more prose on the same line. Treating
# in-paragraph bold emphasis as a section break used to silently
# terminate the AC scan before any following bullet could satisfy
# the validator.
_OTHER_HEADING_RE: re.Pattern[str] = re.compile(
    r"^[ \t]*(?:\#{1,6}[ \t]|\*\*[^*\n]+\*\*[ \t]*:?[ \t]*$)"
)


def has_acceptance_criteria_section(description: str) -> bool:
    """Return True iff ``description`` contains an AC section with at
    least one bullet.

    The "AC section" is the run of lines starting at an AC-section
    heading (per ``_AC_HEADING_RE``) and ending at the next
    heading-style line (markdown ``#``-heading or stand-alone bold
    label per ``_OTHER_HEADING_RE``) or end-of-string. The section is
    satisfied when at least one of those intervening lines is a bullet
    (per ``_BULLET_RE``).

    Interleaved prose (an intro sentence between the heading and the
    first bullet, or an explanatory paragraph between two bullets) is
    tolerated — the section continues to scan for bullets until a real
    heading-style break ends it. Only an explicit section boundary
    pops us out of AC scope, never plain text.

    Empty AC sections (a heading with no bullets, terminated by
    another heading or end-of-string) do NOT satisfy the invariant —
    that's a malformed AC, the same failure mode as a missing one.
    """
    if not description:
        return False
    lines = description.splitlines()
    in_ac = False
    for line in lines:
        if _AC_HEADING_RE.match(line):
            in_ac = True
            continue
        if not in_ac:
            continue
        if _BULLET_RE.match(line):
            return True
        if _OTHER_HEADING_RE.match(line):
            # Hit the next heading or bold-label section break without
            # finding a bullet — this AC section is empty. The
            # remainder of the description might have another AC
            # heading; let the loop continue scanning from here.
            in_ac = False
            continue
        # Anything else (blank lines, prose paragraphs, bold inline
        # emphasis): we stay in the AC section and keep looking for a
        # bullet. The reviewer-test-adequacy reviewer ultimately
        # decides whether the bullets are *meaningful* — the model
        # validator just enforces "there is at least one".
    return False


class TicketTouches(BaseModel):
    """Explicit cross-boundary touch declarations for a ticket.

    Phase 2 dep-graph PR #1: supplements the flat module_id + capability_ids
    pair. The graph builder uses these to root the impact view. Optional;
    defaults to all-empty. Tickets without an explicit touches get a best-
    effort impact view from (module_id, capability_ids).

    SA-tier tickets with empty touches are flagged by the dispatch logic
    as a notable reviewer finding — they probably need explicit declaration.
    """

    model_config = ConfigDict(extra="forbid")

    modules: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    exposed_apis: list[str] = Field(
        default_factory=list, description="'<module>:<name>'"
    )
    emitted_events: list[str] = Field(default_factory=list)
    consumed_events: list[str] = Field(default_factory=list)
    data_stores: list[str] = Field(default_factory=list)
    behavioral_contracts: list[str] = Field(default_factory=list)
    data_contracts: list[str] = Field(default_factory=list)
    routes: list[str] = Field(default_factory=list, description="'<METHOD> <path>'")
    migrations: list[str] = Field(default_factory=list)
    env_vars: list[str] = Field(default_factory=list)


class TicketPlanMetadata(BaseModel):
    """Read-only typed view over the v2 build-plan fields on Ticket.

    TD-5: Ticket has accumulated a sizeable v2 planning surface
    (suite_id / module_id / epic_id / layer / dev_tier / etc.).
    Callers that want a typed value object — e.g. reviewers building
    plan-aware prompts or analytics rolling up by module — can read
    ``ticket.plan_metadata`` instead of touching the flat fields.

    Storage stays flat for now (a wholesale migration would touch
    every read site in the project). Future work can flip the
    canonical representation, then remove the flat fields once all
    callers move to ``plan_metadata``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    suite_id: str | None = None
    module_id: str | None = None
    capability_ids: tuple[str, ...] = ()
    epic_id: str | None = None
    layer: str | None = None
    dev_tier: str | None = None
    reviewer_set: tuple[str, ...] = ()
    risks_addressed: tuple[str, ...] = ()
    done_when: str | None = None


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
    PROFILE = "profile"
    ARCHITECTURE = "architecture"
    PLANNING = "planning"
    CANONICALIZE = "canonicalize"


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
# See docs/v2.0/implementation-plan.md Phase 1 Task B.
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
    # Created through a front door (CLI / standalone MCP) but not yet
    # approved for work. Non-dispatchable: find_ready() is OPEN-only, so a
    # PROPOSED ticket is never picked up until an operator approves it
    # (PROPOSED -> OPEN). Distinct from OPEN (approved, dispatchable).
    PROPOSED = "proposed"
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
    # ``extra="forbid"`` (Block A.3) — typos in operator-edited YAML
    # (e.g. ``visulal_references``) used to land on the model as
    # silently-discarded keys; now they raise at load time. The schema
    # is otherwise additive: every legacy field stays.
    #
    # We MUST NOT set ``populate_by_name=False`` here — ``StoreModel``
    # turns it on so callers can pass ``id=`` (the alias is ``_id``)
    # interchangeably. Re-declare both explicitly.
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    work_type: WorkType
    size: Size = Size.M
    status: TicketStatus = TicketStatus.OPEN
    # Human-usable short handle (``jig-N``), assigned by TicketStore.create.
    # The internal ``id`` (UUID4) stays the canonical reference; ``key`` is an
    # additive alias for CLI/MCP ergonomics. Empty on pre-feature records until
    # backfilled on first front-door access.
    key: str = ""
    title: str
    description: str = ""
    assignee: str | None = None
    derived_from: str | None = None  # e.g. "project://spec/capabilities/due-dates"
    parent_id: str | None = None
    blocks: list[str] = Field(default_factory=list)
    blocked_by: list[str] = Field(default_factory=list)
    workflow: str = "default"
    labels: list[str] = Field(default_factory=list)
    created_by: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # v2 build-plan extensions. Optional during the v2 build so v1 records
    # still load; populated by the Planner PM when the build plan owns the
    # ticket. See docs/v2.0/pm-workflow/design.md §"Ticket structure (extensions)".
    suite_id: str | None = None
    module_id: str | None = None
    capability_ids: list[str] = Field(default_factory=list)
    epic_id: str | None = None
    layer: str | None = None  # bones | mvp | final
    dev_tier: str | None = None  # standard | senior | sa
    reviewer_set: list[str] = Field(default_factory=list)
    context_hints: dict[str, Any] = Field(default_factory=dict)
    risks_addressed: list[str] = Field(default_factory=list)
    done_when: str | None = None
    examples: list[dict[str, str]] = Field(default_factory=list)
    # Phase 2 dep-graph PR #1 — explicit cross-boundary declarations.
    # Optional; graph falls back to (module_id, capability_ids) when empty.
    touches: TicketTouches = Field(default_factory=TicketTouches)

    # v2 Track D MVP — VD wireframes referenced by this ticket. Each
    # entry is a screen-id matching a ``.jig/spec/wireframes/<id>.html``
    # file. The visual_compliance reviewer walks this list to verify
    # the wireframe exists, lints clean, and is referenced in the dev's
    # diff. Empty list means "this ticket implements no UI" — the
    # reviewer is skipped per dispatch logic.
    visual_references: list[str] = Field(default_factory=list)

    # Set when the Coordinator defers this ticket via the DEFERRED queue
    # (per docs/v2.0/pm-workflow/design.md §"DEFERRED queue triage"). The
    # ticket itself stays in the store; this stamp lets downstream views
    # filter "currently deferred" without needing to consult the queue.
    deferred_at: datetime | None = None

    # v2 Track G Final — set when this ticket amends an existing
    # behavioral / data contract as part of its work (per
    # ``docs/v2.0/pm-workflow/design.md`` §"Reviewer federation — selection
    # logic"). When populated, the dispatch logic auto-selects
    # ``reviewer-architectural`` so the SA-tier reviewer can verify
    # the amendment was deliberate. The string is a short rationale
    # (e.g. ``"adds optional retry-policy field to ingest-batch-atomicity"``);
    # the SA reviewer reads it for context. ``None`` means "no contract
    # amendment in this ticket".
    contract_amendment: str | None = None

    # v2 Hardening — structured reason set when the ticket lands in a
    # non-OK terminal state driven by the review-federation gate (per
    # ``docs/v2.0/pm-workflow/design.md`` §"Severity tiers and disposition").
    # Known values:
    #   ``"reviewer-critical"``  — federation found one or more critical
    #                              comments; ticket FAILED.
    #   ``"reviewer-important"`` — federation found one or more important
    #                              comments; ticket BLOCKED awaiting SA
    #                              consult Handoff resolution.
    #   ``"federation-error"``   — federation crashed (after the single
    #                              retry); ticket FAILED.
    # ``None`` for clean transitions and operator-driven parks — that
    # preserves the bones-era contract for the existing BLOCKED /
    # FAILED states. Analytics + the operator UX filter on this field
    # to distinguish review-blocked tickets from operator-blocked ones
    # without reading prose.
    block_reason: str | None = None

    @property
    def plan_metadata(self) -> TicketPlanMetadata:
        """Typed read-only view over the v2 build-plan fields (TD-5).

        New code should prefer this over reaching into the flat
        ``suite_id`` / ``module_id`` / etc. attributes; the underlying
        storage may move to a nested representation in a future cleanup
        without changing this property's contract.
        """
        return TicketPlanMetadata(
            suite_id=self.suite_id,
            module_id=self.module_id,
            capability_ids=tuple(self.capability_ids),
            epic_id=self.epic_id,
            layer=self.layer,
            dev_tier=self.dev_tier,
            reviewer_set=tuple(self.reviewer_set),
            risks_addressed=tuple(self.risks_addressed),
            done_when=self.done_when,
        )

    @field_validator("title")
    @classmethod
    def _strip_title_backticks(cls, value: str) -> str:
        """Drop backticks from ticket titles.

        PM / planner agents write titles with markdown-style backticks
        for flag names (``` `--min-score` ```), code refs, etc. The
        TUI's Tickets tab and sidebar don't render markdown — backticks
        show up literally, and the visual density of ``` `-- ``` reads
        as a strikethrough in many terminal fonts (operator sees a
        non-blocked ticket and assumes it's complete). Stripping at the
        model layer means every consumer (TUI, sidebar, sidebar tail,
        agent prompts, logs, story output) sees the same clean title
        without each renderer having to remember to scrub.
        """
        return value.replace("`", "")

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
                f"Ticket.layer must be one of {sorted(_ALLOWED_LAYERS)!r}, got {v!r}"
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

    @model_validator(mode="after")
    def _require_acceptance_criteria_for_work_tickets(self) -> "Ticket":
        """Work-type tickets must carry an Acceptance Criteria section.

        Enforces the invariant that every FEATURE / BUGFIX / REFACTOR /
        SPIKE / PERF / MIGRATION ticket has a discoverable AC section
        with at least one bullet in its description. System work types
        (BRIEF, ARCHITECTURE, PLANNING, CANONICALIZE, DOCS) orchestrate
        other work and don't carry their own AC, so they're exempt.

        Downstream consumers (reviewer-test-adequacy, the workflow
        loop) can rely on this invariant rather than adding defensive
        fallbacks for malformed tickets.
        """
        # ``self.work_type`` is always a ``WorkType`` enum after Pydantic
        # construction — the field is typed ``WorkType`` and coerced to
        # the enum even when the caller passes a raw string.
        work_type_value = self.work_type.value
        if work_type_value not in _WORK_TYPES_REQUIRING_AC:
            return self
        if not has_acceptance_criteria_section(self.description):
            raise ValueError(
                f"Ticket with work_type={work_type_value!r} must include an "
                "Acceptance Criteria section in its description (a heading "
                "like '## Acceptance criteria', '### Acceptance criteria', "
                "or '**Acceptance criteria:**' followed by at least one "
                "bullet). System types (brief, architecture, planning, "
                "canonicalize, docs) are exempt."
            )
        return self

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
