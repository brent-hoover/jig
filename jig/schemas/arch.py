"""SA output schemas — architecture.yaml + modules/<m>/contracts.yaml.

Bones scope: ``Architecture``, ``Module``, ``ContractsFile``,
``BehavioralContract``, ``DataContract``, ``Risk``, ``ChangeLogEntry``.
The full contract-type union (event, api, schema, error, perf, security,
process — see ``docs/sa-architecture/design.md`` §"Contract types") lands
incrementally; bones uses the two flavors (data + behavioral) the bones
scenario exercises.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from jig.intent import Intent

__all__ = [
    "Architecture",
    "BehavioralContract",
    "CascadeContractDisposition",
    "CascadeProposal",
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


class DevProvisioning(BaseModel):
    """Per-data-store dev-provisioning declaration (Track E MVP).

    Declares the isolation strategy + cleanup policy + connection-string
    template used by the orchestrator's per-agent provisioning hooks.
    Three strategies cover virtually every case (per
    ``docs/dev-environment/design.md`` §"Provisioning strategies"):

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
    kind: str = Field(..., min_length=1, description="postgres | sqlite | opensearch | nats | ...")
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


class CrossCuttingPolicy(BaseModel):
    """Architecture-wide policy that applies to multiple modules."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1)
    polarity: ContractPolarity
    rule: str = Field(..., min_length=1)
    auto_generates_integration_ac: bool = False


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


class ExternalDependency(BaseModel):
    """An external system (API, queue, file source) this module depends on."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1)
    kind: str = Field(..., min_length=1, description="external_http | file_upload | external_queue | ...")
    rate_limit: str | None = None
    auth: str | None = None
    failure_mode: str | None = None
    max_size: str | None = None


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


class BehavioralContract(BaseModel):
    """Design-by-Contract: precondition / postcondition / invariant / side-effect.

    The highest-leverage contract type per ``docs/sa-architecture/design.md``
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


class ContractsFile(BaseModel):
    """One module's contracts file — modules/<m>/contracts.yaml."""

    model_config = ConfigDict(extra="forbid")

    spec_version: int = 1
    module: str = Field(..., min_length=1)
    owns: list[OwnedCollection] = Field(default_factory=list)
    external_dependencies: list[ExternalDependency] = Field(default_factory=list)
    exposes: list[dict] = Field(
        default_factory=list,
        description=(
            "Internal API surface this module exposes; sketch for bones. "
            "Typed schema lands as the API contract type matures."
        ),
    )
    emits: list[dict] = Field(
        default_factory=list,
        description=(
            "Events this module emits; sketch for bones. Typed schema "
            "lands with the event contract type."
        ),
    )
    integration_ac: list[IntegrationAcceptance] = Field(default_factory=list)
    behavioral_contracts: list[BehavioralContract] = Field(default_factory=list)
    data_contracts: list[DataContract] = Field(default_factory=list)
    open_questions: list[OpenQuestion] = Field(default_factory=list)
    change_log: list[ChangeLogEntry] = Field(default_factory=list)


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


class CascadeProposal(BaseModel):
    """One cascade-after-confirmed-impossible proposal artifact.

    Persisted to ``.jig/arch/cascades/<risk-id>-<timestamp>.yaml`` per
    ``docs/sa-architecture/design.md`` §"The cascade workflow" step 5
    (audit trail). MVP scope writes the artifact and surfaces it via a
    Handoff on the architecture ticket; the operator hand-edits to set
    real dispositions and re-runs SA. Full transactional confirmation
    + auto-cascade-application lands in Final.
    """

    model_config = ConfigDict(extra="forbid")

    risk_id: str = Field(..., min_length=1)
    spike_ticket_id: str = Field(..., min_length=1)
    finding: str = Field(..., min_length=1)
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    contracts: list[CascadeContractDisposition] = Field(default_factory=list)
