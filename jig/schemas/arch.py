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
    "ChangeLogEntry",
    "ContractsFile",
    "ContractPolarity",
    "CrossCuttingPolicy",
    "DataContract",
    "DataStore",
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


class DataStore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1)
    kind: str = Field(..., min_length=1, description="postgres | sqlite | opensearch | nats | ...")
    rationale: str | None = None
    accessed_by: list[str] = Field(default_factory=list)


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
