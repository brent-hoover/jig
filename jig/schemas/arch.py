"""SA output schemas — architecture.yaml + modules/<m>/contracts.yaml.

Bones scope: ``Architecture``, ``Module``, ``ContractsFile``,
``BehavioralContract``, ``DataContract``, ``Risk``, ``ChangeLogEntry``.
The full contract-type union (event, api, schema, error, perf, security,
process — see ``docs/v2.0/sa-architecture/design.md`` §"Contract types") lands
incrementally; bones uses the two flavors (data + behavioral) the bones
scenario exercises.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from jig.intent import Intent
from jig.schemas._validators import (
    ServiceKind,
    validate_kebab_id,
    validate_project_uri_shape,
    validate_tz_aware,
)


# Risk status values >= ``spike_proposed`` trigger the cascade-prep gates
# (``dependent_contracts`` + ``intent`` required) per
# ``docs/v2.0/sa-architecture/design.md`` §"Risk schema requires
# ``dependent_contracts``". ``OPEN`` is the noted-but-uncommitted state
# that escapes the gate so the SA can capture nascent risks without
# pre-committing to the dependency map.
_RISK_CASCADE_PREP_STATUSES: frozenset[str] = frozenset(
    {
        "spike_proposed",
        "spike_running",
        "mitigated",
        "mitigated_with_constraints",
        "accepted",
        "confirmed_impossible",
    }
)

__all__ = [
    "Architecture",
    "BehavioralContract",
    "CascadeAuditEntry",
    "CascadeContractDisposition",
    "CascadeProposal",
    "CascadeStage",
    "CascadeState",
    "ChangeLogEntry",
    "ContractsFile",
    "ContractPolarity",
    "CrossCuttingPolicy",
    "DataContract",
    "DataStore",
    "DevProvisioning",
    "ExternalDependency",
    "IntegrationAcceptance",
    "Module",
    "OpenQuestion",
    "OwnedCollection",
    "Risk",
    "RiskImpact",
    "RiskLikelihood",
    "RiskStatus",
    "SharedContract",
    "TierHint",
]


class TierHint(str, Enum):
    STANDARD = "standard"
    SENIOR = "senior"
    SA = "sa"


class ContractPolarity(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"


class RiskImpact(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RiskLikelihood(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RiskStatus(str, Enum):
    OPEN = "open"
    SPIKE_PROPOSED = "spike_proposed"
    SPIKE_RUNNING = "spike_running"
    MITIGATED = "mitigated"
    # Track C Final per ``docs/v2.0/sa-architecture/design.md`` §"Failure modes
    # and mitigations" mitigation #3: a spike that returns "depends on
    # operator constraint X" rather than impossible/mitigated transitions
    # the risk to this state. The cascade fires conditionally on the
    # constraint being met — captured in the cascade artifact's
    # ``constraint`` field rather than mutating contracts unconditionally.
    MITIGATED_WITH_CONSTRAINTS = "mitigated_with_constraints"
    ACCEPTED = "accepted"
    CONFIRMED_IMPOSSIBLE = "confirmed_impossible"


class ChangeLogEntry(BaseModel):
    """One entry in an artifact's change_log; powers @revision:N URIs."""

    model_config = ConfigDict(extra="forbid")

    revision: int = Field(..., ge=1)
    date: date
    summary: str = Field(..., min_length=1)


class OpenQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1)
    text: str = Field(..., min_length=1)
    blocking: list[str] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _kebab_id(cls, v: str) -> str:
        return validate_kebab_id(v, "OpenQuestion.id")


class DevProvisioning(BaseModel):
    """Per-data-store dev-provisioning declaration (Track E MVP).

    Declares the isolation strategy + cleanup policy + connection-string
    template used by the orchestrator's per-agent provisioning hooks.
    Three strategies cover virtually every case (per
    ``docs/v2.0/dev-environment/design.md`` §"Provisioning strategies"):

    - ``shared_namespaced`` — one shared service instance, per-agent
      namespace prefix (Postgres CREATE SCHEMA, NATS subject prefix,
      S3 bucket prefix, Redis key prefix). MVP scope ships this.
    - ``per_agent_ephemeral`` — each agent gets its own ephemeral
      instance (SQLite per file, per-agent Postgres DB). Schema-only
      stub in MVP scope; concrete provisioner lands in Final.
    - ``operator_supplied`` — operator owns the service out-of-band;
      jig only injects the connection string. Schema-only stub.

    ``namespace_template`` supports ``{agent_id}``, ``{ticket_id}``,
    and ``{epic_id}`` placeholders. ``connection_string_template``
    additionally supports ``{namespace}`` so the per-agent prefix lands
    inside the URL exactly where it needs to (e.g. Postgres
    ``search_path``, S3 bucket prefix, NATS subject root).
    """

    model_config = ConfigDict(extra="forbid")

    strategy: Literal[
        "shared_namespaced", "per_agent_ephemeral", "operator_supplied"
    ]
    namespace_template: str = Field(
        default="agent_{agent_id}_{ticket_id}",
        min_length=1,
        description=(
            "Substituted with {agent_id} / {ticket_id} / {epic_id} at "
            "provision time to produce the per-agent namespace prefix."
        ),
    )
    cleanup_on_success: Literal["drop", "archive", "keep"] = "drop"
    cleanup_on_failure: Literal["drop", "archive", "keep"] = "archive"
    connection_string_template: str = Field(
        default="",
        description=(
            "Per-service URL template; supports {namespace} along with "
            "the same {agent_id} / {ticket_id} / {epic_id} placeholders "
            "as ``namespace_template``. Example for Postgres: "
            '"postgresql://jig:jig@localhost:5432/jigdev'
            '?options=-c%20search_path%3D{namespace}". MVP-shipped '
            "kinds (NATS / Redis / S3) treat this as the prefix-bearing "
            "URL the agent's code reads from env."
        ),
    )


class DataStore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1)
    kind: ServiceKind = Field(
        ..., description="Bounded vocabulary; see jig.schemas._validators.ServiceKind"
    )
    rationale: str | None = None
    accessed_by: list[str] = Field(default_factory=list)
    dev_provisioning: DevProvisioning | None = Field(
        default=None,
        description=(
            "Optional Track E MVP dev-provisioning block. Absence means "
            "the orchestrator's per-agent provisioning step skips this "
            "store (operator-shared / no isolation needed). Presence "
            "names the strategy and connection-string template the "
            "provisioner uses."
        ),
    )

    @field_validator("id")
    @classmethod
    def _kebab_id(cls, v: str) -> str:
        return validate_kebab_id(v, "DataStore.id")


class SharedContract(BaseModel):
    """An entry in architecture.yaml's shared_contracts list."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1)
    type: Literal["data", "event", "api", "schema", "error", "perf", "security", "process"]
    description: str | None = None
    schema_ref: str | None = None
    payload_ref: str | None = None
    publisher: str | None = None
    subscribers: list[str] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _kebab_id(cls, v: str) -> str:
        return validate_kebab_id(v, "SharedContract.id")

    @field_validator("schema_ref", "payload_ref")
    @classmethod
    def _uri_shape(cls, v: str | None) -> str | None:
        return validate_project_uri_shape(v) if v is not None else v


class CrossCuttingPolicy(BaseModel):
    """Architecture-wide policy that applies to multiple modules."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1)
    polarity: ContractPolarity
    rule: str = Field(..., min_length=1)
    auto_generates_integration_ac: bool = False

    @field_validator("id")
    @classmethod
    def _kebab_id(cls, v: str) -> str:
        return validate_kebab_id(v, "CrossCuttingPolicy.id")


class Risk(BaseModel):
    """A risk logged by SA during architecture authoring.

    ``dependent_contracts`` is required once status >= ``spike_proposed``;
    cascade-after-impossible-spike depends on knowing which contracts a
    confirmed-impossible spike invalidates.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1)
    text: str = Field(..., min_length=1)
    impact: RiskImpact
    likelihood: RiskLikelihood
    status: RiskStatus
    spike_ticket: str | None = None
    accepted_if: str | None = None
    blocking: list[str] = Field(default_factory=list)
    dependent_contracts: list[str] = Field(
        default_factory=list,
        description=(
            "URIs to contracts whose viability depends on this risk. "
            "Required once status >= spike_proposed."
        ),
    )
    cascade_breaking_likely: bool = False
    intent: Intent | None = Field(
        default=None,
        description="Required for risks with status >= spike_proposed.",
    )

    @field_validator("id")
    @classmethod
    def _kebab_id(cls, v: str) -> str:
        return validate_kebab_id(v, "Risk.id")

    @field_validator("dependent_contracts")
    @classmethod
    def _uri_shape_dependent_contracts(cls, v: list[str]) -> list[str]:
        for entry in v:
            validate_project_uri_shape(entry)
        return v

    @model_validator(mode="after")
    def _enforce_cascade_prep_invariants(self) -> Risk:
        """Once a risk transitions past ``open``, the cascade workflow
        needs ``dependent_contracts`` + ``intent`` declared up front.

        Mirrors the v2 SA design's "Risk schema requires
        ``dependent_contracts``" rule, hoisted from the upsert handler
        in ``jig.sa_incremental_mcp`` to the schema layer so any
        construction path (YAML load, unit-test fixture, MCP arg
        coercion) fails the same way at the same boundary. ``OPEN``
        skips this check so an SA can capture nascent risks without
        pre-committing to the dependency map.
        """
        # Compare against the string value so the gate works whether
        # ``status`` arrives as the enum or its raw string form.
        status_value = (
            self.status.value
            if isinstance(self.status, RiskStatus)
            else str(self.status)
        )
        if status_value not in _RISK_CASCADE_PREP_STATUSES:
            return self
        if not self.dependent_contracts:
            raise ValueError(
                f"Risk.dependent_contracts must be non-empty when "
                f"status is {status_value!r} (>= spike_proposed). The "
                "cascade workflow needs the dependents enumerated up "
                "front so a confirmed-impossible spike can amend them."
            )
        if self.intent is None:
            raise ValueError(
                f"Risk.intent must be set when status is "
                f"{status_value!r} (>= spike_proposed). Every v2 "
                "artifact past the early-capture state carries an "
                "intent layer; risks aren't an exception."
            )
        return self


class ApiConsumption(BaseModel):
    """Declares that this module calls an API exposed by another module."""

    model_config = ConfigDict(extra="forbid")

    module: str = Field(..., min_length=1, description="Provider module id.")
    name: str = Field(
        ..., min_length=1, description="API name as declared in provider's exposes[]."
    )


class EventConsumption(BaseModel):
    """Declares that this module subscribes to an event emitted by another module."""

    model_config = ConfigDict(extra="forbid")

    module: str = Field(..., min_length=1, description="Publisher module id.")
    name: str = Field(
        ..., min_length=1, description="Event name as declared in publisher's emits[]."
    )


class Module(BaseModel):
    """A module entry in architecture.yaml."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    summary: str = Field(..., min_length=1)
    implements_capabilities: list[str] = Field(default_factory=list)
    owns: list[str] = Field(
        default_factory=list,
        description="Collection ids this module owns (write_access[self]).",
    )
    tier_hint: TierHint = TierHint.STANDARD
    requires_tracer_bullet: bool = False
    n_a_categories: list[str] = Field(
        default_factory=list,
        description=(
            "Checklist categories this module legitimately doesn't address "
            "(e.g. ``behavioral_contracts`` for a CRUD-only data module). "
            "Required for ``arch_finalize`` to skip checklist enforcement "
            "on those categories — silence is treated as 'forgot to "
            "address', not 'no behavioral contracts apply'."
        ),
    )
    intent: Intent = Field(
        ...,
        description="Why this module exists; the simplest-it-could-be version.",
    )
    cascade_risk_low: bool = Field(
        default=False,
        description=(
            "SA-suggested hint per ``docs/v2.0/pm-workflow/design.md`` "
            "§'Bones-first ordering' / cascade_risk_low flag: when "
            "True, even if this module's bones doesn't converge "
            "cleanly, promoting it to MVP is unlikely to cascade "
            "upward (no new shared shapes, no new contracts, no "
            "flagged risks). PM Coordinator reads this to allow MVP "
            "promotion on unblocked epics when blocked epics touch "
            "only cascade_risk_low=true modules."
        ),
    )
    cascade_risk_low_rationale: str | None = Field(
        default=None,
        description=(
            "Why the SA judged cascade risk as low. Required when "
            "``cascade_risk_low`` is True so the operator (and the "
            "audit trail) can see the reasoning rather than a bare "
            "boolean toggle."
        ),
    )
    # Phase 2 dep-graph PR #1 — declared consumption edges. The graph
    # builder uses these to construct consumes edges without static
    # import analysis. arch_finalize validates that each entry resolves
    # to a real ExposedAPI / EmittedEvent on the named provider.
    consumes_apis: list[ApiConsumption] = Field(default_factory=list)
    consumes_events: list[EventConsumption] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _kebab_id(cls, v: str) -> str:
        return validate_kebab_id(v, "Module.id")

    @model_validator(mode="after")
    def _enforce_cascade_risk_low_rationale(self) -> Module:
        """When ``cascade_risk_low=True``, the operator (and the audit
        trail) need to see the SA's reasoning rather than a bare boolean.

        The minimum-prose floor (>= 10 chars) is small but enough to
        keep "ok" / "n/a" / single-token prose out of the artifact —
        the rationale exists so PM coordinators reading the cascade
        risk override can audit it later, and a one-word rationale
        defeats that purpose.
        """
        if not self.cascade_risk_low:
            return self
        rationale = self.cascade_risk_low_rationale
        if rationale is None or len(rationale.strip()) < 10:
            raise ValueError(
                "Module.cascade_risk_low_rationale must be a non-empty "
                "string (>= 10 chars) when cascade_risk_low is True. "
                "The PM coordinator reads this when deciding whether "
                "to honor the SA's override; bare booleans defeat the "
                "audit trail."
            )
        return self


def _check_unique_ids(
    items: list, *, attr: str, owner: str, collection: str
) -> None:
    """Raise when two entries in ``items`` share the same ``attr`` value.

    Hoisted helper because every aggregate model in this package wants
    the same shape: collection name + entry attribute name + owner
    name to put in the error message. Centralizing the check keeps the
    error format uniform across schemas.
    """
    seen: set[str] = set()
    duplicates: set[str] = set()
    for item in items:
        value = getattr(item, attr)
        if value in seen:
            duplicates.add(value)
        else:
            seen.add(value)
    if duplicates:
        raise ValueError(
            f"{owner}.{collection}: duplicate {attr} value(s) "
            f"{sorted(duplicates)!r}. Downstream consumers key off "
            f"{attr} and a duplicate breaks ticket / review / "
            f"reviewer dispatch."
        )


class Architecture(BaseModel):
    """Top-level architecture.yaml. Lives at .jig/spec/architecture.yaml."""

    model_config = ConfigDict(extra="forbid")

    spec_version: int = 1
    data_stores: list[DataStore] = Field(default_factory=list)
    modules: list[Module] = Field(default_factory=list)
    shared_contracts: list[SharedContract] = Field(default_factory=list)
    cross_cutting_policies: list[CrossCuttingPolicy] = Field(default_factory=list)
    risks: list[Risk] = Field(default_factory=list)
    open_questions: list[OpenQuestion] = Field(default_factory=list)
    change_log: list[ChangeLogEntry] = Field(default_factory=list)
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @field_validator("generated_at")
    @classmethod
    def _tz_generated_at(cls, v: datetime) -> datetime:
        return validate_tz_aware(v, "Architecture.generated_at")

    @model_validator(mode="after")
    def _enforce_id_uniqueness(self) -> Architecture:
        """Every id-bearing list inside the architecture is keyed on
        ``id`` by downstream consumers (PM, reviewers, MCP handlers).
        Duplicates silently route work to the wrong entry — pin
        uniqueness at the schema layer so a hand-edited YAML can't
        sneak the breakage in."""
        _check_unique_ids(
            self.data_stores, attr="id",
            owner="Architecture", collection="data_stores",
        )
        _check_unique_ids(
            self.modules, attr="id",
            owner="Architecture", collection="modules",
        )
        _check_unique_ids(
            self.shared_contracts, attr="id",
            owner="Architecture", collection="shared_contracts",
        )
        _check_unique_ids(
            self.cross_cutting_policies, attr="id",
            owner="Architecture", collection="cross_cutting_policies",
        )
        _check_unique_ids(
            self.risks, attr="id",
            owner="Architecture", collection="risks",
        )
        _check_unique_ids(
            self.open_questions, attr="id",
            owner="Architecture", collection="open_questions",
        )
        return self


# ---------------------------------------------------------------------------
# Per-module contracts (modules/<m>/contracts.yaml)
# ---------------------------------------------------------------------------


class OwnedCollection(BaseModel):
    """A collection this module owns. Other modules read via the owner's API."""

    model_config = ConfigDict(extra="forbid")

    collection: str = Field(..., min_length=1)
    db: str = Field(..., min_length=1)
    schema_ref: str | None = None
    write_access: list[str] = Field(default_factory=lambda: ["self"])
    read_access: list[str] = Field(default_factory=list)

    @field_validator("schema_ref")
    @classmethod
    def _uri_shape_schema_ref(cls, v: str | None) -> str | None:
        return validate_project_uri_shape(v) if v is not None else v


class ExternalDependency(BaseModel):
    """An external system (API, queue, file source) this module depends on."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1)
    kind: str = Field(..., min_length=1, description="external_http | file_upload | external_queue | ...")
    rate_limit: str | None = None
    auth: str | None = None
    failure_mode: str | None = None
    max_size: str | None = None

    @field_validator("id")
    @classmethod
    def _kebab_id(cls, v: str) -> str:
        return validate_kebab_id(v, "ExternalDependency.id")


class IntegrationAcceptance(BaseModel):
    """Per-capability MUST list — the integration AC for one capability."""

    model_config = ConfigDict(extra="forbid")

    capability: str = Field(..., min_length=1)
    must: list[str] = Field(default_factory=list)


class DataContract(BaseModel):
    """A data shape contract (vs behavioral). Often references an OpenAPI/SQL/Pydantic schema.

    ``fields`` (Track I MVP) carries an inline {field_name: type_str} mapping
    so the Pydantic-from-data-contract renderer has something to render
    against without resolving ``schema_ref``. URI-based field resolution
    lands when the URI scheme matures.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1)
    type: Literal["data"] = "data"
    description: str | None = None
    schema_ref: str | None = None
    fields: dict[str, str] | None = Field(
        default=None,
        description=(
            "Inline {field_name: type_str} map — type_str is a simple "
            'Python type expression like "str", "int", "list[str]", or '
            '"int | None". Used by the Pydantic renderer (Track I MVP); '
            "kept optional so legacy contracts that only carry a "
            "``schema_ref`` continue to validate."
        ),
    )
    intent: Intent

    @field_validator("id")
    @classmethod
    def _kebab_id(cls, v: str) -> str:
        return validate_kebab_id(v, "DataContract.id")

    @field_validator("schema_ref")
    @classmethod
    def _uri_shape_schema_ref(cls, v: str | None) -> str | None:
        return validate_project_uri_shape(v) if v is not None else v

    @model_validator(mode="after")
    def _enforce_shape_present(self) -> DataContract:
        """A data contract carries a shape — either a URI to an external
        schema (``schema_ref``) or an inline ``fields`` map. A contract
        with neither is meaningless: the Pydantic renderer has nothing
        to render and the operator has nothing to inspect.
        """
        if self.schema_ref is None and not self.fields:
            raise ValueError(
                "DataContract must declare at least one of schema_ref "
                "or fields. A data contract with no shape can't be "
                "rendered or audited."
            )
        return self


class BehavioralContract(BaseModel):
    """Design-by-Contract: precondition / postcondition / invariant / side-effect.

    The highest-leverage contract type per ``docs/v2.0/sa-architecture/design.md``
    §"Behavioral contracts". Postconditions translate directly to test
    assertions; invariants to static checks; side-effects to "did the diff
    include the required call?" — all mechanically reviewable.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1)
    type: Literal["behavioral"] = "behavioral"
    applies_to: dict[str, str] | None = Field(
        default=None,
        description='e.g. {"capability": "shopify-connect", "module": "catalog-ingest"}',
    )
    scope: str | None = Field(
        default=None,
        description='e.g. "every-query", "cross_cutting"',
    )
    precondition: str | None = None
    postcondition: str | None = None
    invariant: str | None = None
    side_effects: list[str] = Field(default_factory=list)
    side_effect_required: str | None = None
    enforcement: str | None = None
    intent: Intent

    @field_validator("id")
    @classmethod
    def _kebab_id(cls, v: str) -> str:
        return validate_kebab_id(v, "BehavioralContract.id")

    @model_validator(mode="after")
    def _enforce_some_constraint(self) -> BehavioralContract:
        """A behavioral contract must constrain at least one thing.

        Mirrors the design-by-contract intent: at least one of
        ``precondition``, ``postcondition``, ``invariant``,
        ``side_effects`` (non-empty), or ``side_effect_required`` must
        be populated. A contract with none of these constrains nothing
        and isn't worth the artifact slot.
        """
        if not (
            self.precondition
            or self.postcondition
            or self.invariant
            or self.side_effects
            or self.side_effect_required
        ):
            raise ValueError(
                "BehavioralContract must populate at least one of "
                "precondition, postcondition, invariant, side_effects "
                "(non-empty), or side_effect_required. A contract that "
                "constrains nothing is meaningless."
            )
        return self


class ExposedAPI(BaseModel):
    """One entry in ``ContractsFile.exposes`` — an internal API the
    module makes available to other modules. Replaces the bones-era
    ``list[dict]`` so reviewers can rely on a known shape (TD-1)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        ...,
        min_length=1,
        description=(
            "Public identifier the module exposes (function name, "
            "class name, endpoint path, CLI subcommand, etc.)."
        ),
    )
    kind: Literal[
        "function", "class", "endpoint", "cli",
        "route", "migration", "env_var",
        "other",
    ] = "function"
    summary: str = Field(
        ...,
        min_length=1,
        description=(
            "One-line summary of what the API does — enough for a "
            "reviewer to tell whether a downstream module is using "
            "the right surface."
        ),
    )
    schema_ref: str | None = Field(
        default=None,
        description=(
            "Optional URI to a richer schema (contracts.yaml entry, "
            "OpenAPI fragment, etc.) describing argument/return shape."
        ),
    )


class EmittedEvent(BaseModel):
    """One entry in ``ContractsFile.emits`` — an event the module
    publishes onto the bus or analytics stream (TD-1)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        ...,
        min_length=1,
        description="Event name. Kebab-case by convention.",
    )
    summary: str = Field(
        ...,
        min_length=1,
        description="One-line description of when the event fires.",
    )
    schema_ref: str | None = Field(
        default=None,
        description=(
            "Optional URI to the event payload schema, if formalised."
        ),
    )


class ContractsFile(BaseModel):
    """One module's contracts file — modules/<m>/contracts.yaml."""

    model_config = ConfigDict(extra="forbid")

    spec_version: int = 1
    module: str = Field(..., min_length=1)
    owns: list[OwnedCollection] = Field(default_factory=list)
    external_dependencies: list[ExternalDependency] = Field(default_factory=list)
    exposes: list[ExposedAPI] = Field(
        default_factory=list,
        description=(
            "Internal API surface this module exposes. Each entry "
            "names a function/class/endpoint and a one-line summary."
        ),
    )
    emits: list[EmittedEvent] = Field(
        default_factory=list,
        description=(
            "Events this module emits. Each entry names the event "
            "and a one-line summary of when it fires."
        ),
    )
    integration_ac: list[IntegrationAcceptance] = Field(default_factory=list)
    behavioral_contracts: list[BehavioralContract] = Field(default_factory=list)
    data_contracts: list[DataContract] = Field(default_factory=list)
    open_questions: list[OpenQuestion] = Field(default_factory=list)
    change_log: list[ChangeLogEntry] = Field(default_factory=list)

    @field_validator("module")
    @classmethod
    def _kebab_module(cls, v: str) -> str:
        return validate_kebab_id(v, "ContractsFile.module")

    @model_validator(mode="after")
    def _enforce_id_uniqueness(self) -> ContractsFile:
        """Per-list uniqueness inside a module's contracts file.

        ``behavioral_contracts`` / ``data_contracts`` keyed on ``id``;
        ``owns`` keyed on ``collection`` (no ``id`` field — collection
        name is the natural key); ``external_dependencies`` on ``id``;
        ``integration_ac`` on ``capability`` (one per capability).
        """
        _check_unique_ids(
            self.behavioral_contracts, attr="id",
            owner="ContractsFile", collection="behavioral_contracts",
        )
        _check_unique_ids(
            self.data_contracts, attr="id",
            owner="ContractsFile", collection="data_contracts",
        )
        _check_unique_ids(
            self.owns, attr="collection",
            owner="ContractsFile", collection="owns",
        )
        _check_unique_ids(
            self.external_dependencies, attr="id",
            owner="ContractsFile", collection="external_dependencies",
        )
        _check_unique_ids(
            self.integration_ac, attr="capability",
            owner="ContractsFile", collection="integration_ac",
        )
        _check_unique_ids(
            self.exposes, attr="name",
            owner="ContractsFile", collection="exposes",
        )
        _check_unique_ids(
            self.emits, attr="name",
            owner="ContractsFile", collection="emits",
        )
        return self


# ---------------------------------------------------------------------------
# Cascade-after-confirmed-impossible artifact (Track C MVP follow-on)
# ---------------------------------------------------------------------------


class CascadeContractDisposition(BaseModel):
    """One row in a cascade proposal: a dependent contract + proposed action.

    The MVP cascade-generator writes ``proposed_disposition='still_holds'``
    by default for every dependent and lets the operator hand-edit the
    YAML to flip entries to ``invalidated`` (the contract is wrong; replace)
    or ``needs_revision`` (the contract still applies but with new
    constraints). Full automatic disposition lands in Final per the
    design's §"The cascade workflow" SA delta-pass step.
    """

    model_config = ConfigDict(extra="forbid")

    uri: str = Field(..., min_length=1)
    current_shape: str | None = Field(
        default=None,
        description=(
            "Snapshot of the contract's current text/shape at cascade-"
            "generation time. Optional because the URI authority "
            "resolvers are partial in MVP — the writer fills what it "
            "can resolve, leaves None where the URI doesn't resolve "
            "(operator still sees the URI and can read the source)."
        ),
    )
    proposed_disposition: Literal[
        "invalidated", "needs_revision", "still_holds"
    ] = "still_holds"

    @field_validator("uri")
    @classmethod
    def _uri_shape(cls, v: str) -> str:
        return validate_project_uri_shape(v)


class CascadeStage(BaseModel):
    """One stage of a (potentially staged) cascade proposal.

    Per ``docs/v2.0/sa-architecture/design.md`` §"Failure modes and
    mitigations" failure mode #2 — when a cascade affects N+ contracts,
    the operator gets a per-stage approval flow instead of one
    overwhelming all-or-nothing screen. Each stage is a sub-batch with
    its own approval state; the cascade as a whole resolves only when
    every stage is either approved or rejected.
    """

    model_config = ConfigDict(extra="forbid")

    stage_id: str = Field(..., min_length=1)
    contracts: list[CascadeContractDisposition] = Field(default_factory=list)
    approved: bool = False
    approved_at: datetime | None = None
    approved_by: str | None = None

    @field_validator("approved_at")
    @classmethod
    def _tz_approved_at(cls, v: datetime | None) -> datetime | None:
        return validate_tz_aware(v, "CascadeStage.approved_at") if v else v


class CascadeState(str, Enum):
    """Lifecycle state of a CascadeProposal artifact.

    ``pending`` — newly written; awaiting operator action.
    ``staged`` — split into stages via ``arch_stage_cascade``; partial
    approvals tracked per-stage.
    ``holding`` — concurrent-cascade detection blocked emission per
    failure mode #4 (overlapping dependent_contracts with an in-flight
    cascade).
    ``rejected`` — operator rejected the whole cascade; the audit entry
    captures the reason.
    ``resolved`` — every stage approved (or non-staged cascade
    accepted); contracts will be amended in the SA delta-pass.
    """

    PENDING = "pending"
    STAGED = "staged"
    HOLDING = "holding"
    REJECTED = "rejected"
    RESOLVED = "resolved"


class CascadeAuditEntry(BaseModel):
    """One row in ``.jig/arch/cascades/audit.jsonl``.

    Captures every operator-facing transition on a cascade proposal —
    proposed, staged, stage-approved, rejected, held-for-overlap. The
    JSONL is append-only so cross-project analytics can scan for
    operator-rejection patterns ("operator rejects 30% of cascades from
    this risk family") per failure mode #1.
    """

    model_config = ConfigDict(extra="forbid")

    cascade_id: str = Field(..., min_length=1)
    risk_id: str = Field(..., min_length=1)
    action: Literal[
        "proposed",
        "staged",
        "stage_approved",
        "rejected",
        "holding",
        "resolved",
    ]
    actor: str = Field(..., min_length=1)
    reason: str | None = None
    stage_id: str | None = None
    holding_for: str | None = None
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @field_validator("timestamp")
    @classmethod
    def _tz_timestamp(cls, v: datetime) -> datetime:
        return validate_tz_aware(v, "CascadeAuditEntry.timestamp")


class CascadeProposal(BaseModel):
    """One cascade-after-confirmed-impossible proposal artifact.

    Persisted to ``.jig/arch/cascades/<risk-id>-<timestamp>.yaml`` per
    ``docs/v2.0/sa-architecture/design.md`` §"The cascade workflow" step 5
    (audit trail). Final scope adds ``state`` + optional ``stages`` +
    ``holding_for`` + ``constraint`` to track the four failure-mode
    mitigations on the artifact itself; the JSONL audit log carries
    the per-action history.
    """

    model_config = ConfigDict(extra="forbid")

    cascade_id: str = Field(
        ...,
        min_length=1,
        description=(
            "Unique cascade id, formatted ``<risk-id>-<timestamp>`` to "
            "match the on-disk filename and let the audit trail link "
            "actions to one cascade unambiguously."
        ),
    )
    risk_id: str = Field(..., min_length=1)
    spike_ticket_id: str = Field(..., min_length=1)
    finding: str = Field(..., min_length=1)
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    contracts: list[CascadeContractDisposition] = Field(default_factory=list)
    state: CascadeState = CascadeState.PENDING
    stages: list[CascadeStage] = Field(
        default_factory=list,
        description=(
            "When non-empty, the cascade has been split via "
            "``arch_stage_cascade``. ``contracts`` is preserved as the "
            "flat view; each stage references a subset by URI."
        ),
    )
    holding_for: str | None = Field(
        default=None,
        description=(
            "When set, names the cascade_id of an in-flight cascade "
            "with overlapping dependent_contracts. The new cascade "
            "stays in ``holding`` state until the named one resolves "
            "(failure mode #4 mitigation)."
        ),
    )
    constraint: str | None = Field(
        default=None,
        description=(
            "When the originating risk transitioned to "
            "``mitigated_with_constraints``, the constraint clause "
            "captured here so the cascade fires conditionally rather "
            "than reshaping contracts unconditionally."
        ),
    )
    rejected_reason: str | None = Field(
        default=None,
        description=(
            "Set by ``arch_reject_cascade``. Structured operator "
            "rationale; downstream analytics aggregate by this field "
            "to flag rejection patterns."
        ),
    )

    @field_validator("risk_id")
    @classmethod
    def _kebab_risk_id(cls, v: str) -> str:
        return validate_kebab_id(v, "CascadeProposal.risk_id")

    @field_validator("spike_ticket_id")
    @classmethod
    def _kebab_spike_ticket_id(cls, v: str) -> str:
        return validate_kebab_id(v, "CascadeProposal.spike_ticket_id")

    @field_validator("generated_at")
    @classmethod
    def _tz_generated_at(cls, v: datetime) -> datetime:
        return validate_tz_aware(v, "CascadeProposal.generated_at")

    @model_validator(mode="after")
    def _enforce_state_invariants(self) -> CascadeProposal:
        """Each non-default state pulls one extra field along with it.

        The artifact-as-source-of-truth claim falls apart if a
        ``rejected`` cascade has no reason, a ``holding`` cascade has
        no in-flight pointer, or a ``staged`` cascade has no stages.
        Pinning the implication at the schema layer means a hand-edit
        of the YAML can't quietly drop the field.
        """
        state_value = (
            self.state.value
            if isinstance(self.state, CascadeState)
            else str(self.state)
        )
        if state_value == CascadeState.HOLDING.value and self.holding_for is None:
            raise ValueError(
                "CascadeProposal.holding_for must be set when state is "
                "'holding'. The state names the in-flight overlap; the "
                "field names the cascade we're holding behind."
            )
        if state_value == CascadeState.REJECTED.value and self.rejected_reason is None:
            raise ValueError(
                "CascadeProposal.rejected_reason must be set when state "
                "is 'rejected'. Silent rejections defeat the audit trail."
            )
        if state_value == CascadeState.STAGED.value and not self.stages:
            raise ValueError(
                "CascadeProposal.stages must be non-empty when state is "
                "'staged'. The state advertises a per-stage approval "
                "flow; an empty stages list contradicts that."
            )
        return self
