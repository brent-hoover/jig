"""Assertion union for the bones synthetic operator (Track H4).

Bones ships the five assertion kinds the bones scenario actually checks:

- ``artifact_written`` — a file path exists, optionally containing a
  substring or matching a regex
- ``analytics_event_emitted`` — at least one analytics event of the
  given kind was emitted, optionally with field-value constraints
- ``ticket_status`` — a ticket in the store reached a given status
- ``reviewer_returned_no_critical`` — a reviewer's return list contains
  no comments with severity ``critical``
- ``cost_under_budget`` — total cost stayed under N USD (skipped in
  mock mode where there's no LLM cost)

The full assertion taxonomy from ``docs/v2.0/synthetic-operator/design.md``
§"Assertion framework" is a 13-kind superset; bones implements only
what the bones scenario actually needs. Adding new kinds is one entry
in the union + a check function in ``driver.evaluate_assertion``.

We use a Pydantic ``TypeAdapter`` rather than wrapping the union in a
``BaseModel`` because the YAML loader passes raw dicts to
``ScenarioAssertion.validate_python``; the TypeAdapter shape mirrors
the entry-point in ``jig.analytics.events.parse_event``.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

__all__ = [
    "AnalyticsEventEmittedAssertion",
    "ArtifactWrittenAssertion",
    "AssertionKind",
    "BuildPlanLayerStatusAssertion",
    "CascadeProposalAssertion",
    "ContractValidatedAssertion",
    "CostUnderBudgetAssertion",
    "DiscoveryStateConsistentAssertion",
    "EnvVarSetAssertion",
    "EnvelopeUpdatedAssertion",
    "FixtureCassetteAssertion",
    "OntologyTermAssertion",
    "OrphanReportAssertion",
    "ProvisioningSucceededAssertion",
    "ReviewCommentInStoreAssertion",
    "ReviewerReturnedNoCriticalAssertion",
    "RiskStatusAssertion",
    "ScenarioAssertion",
    "ScenarioAssertionUnion",
    "TicketStatusAssertion",
    "TierPromotionAssertion",
    "WireframeAssertion",
]


class AssertionKind(str, Enum):
    """Discriminator values for the bones assertion union."""

    ARTIFACT_WRITTEN = "artifact_written"
    ANALYTICS_EVENT_EMITTED = "analytics_event_emitted"
    TICKET_STATUS = "ticket_status"
    REVIEWER_RETURNED_NO_CRITICAL = "reviewer_returned_no_critical"
    COST_UNDER_BUDGET = "cost_under_budget"
    # Block 2 — verify a sim step stamped an env-var entry on the
    # driver context (used to gate JIG_FIXTURE_MODE-style spawn-env
    # checks without coupling assertions to live process env).
    ENV_VAR_SET = "env_var_set"
    # Block 3 (federation) — assert that a comment from the named
    # reviewer is present in the live ReviewCommentsStore for a given
    # ticket. Pins the gap the v2-review flagged: pre-Block-3 the
    # specialty scenario only verified selection, not execution.
    REVIEW_COMMENT_IN_STORE = "review_comment_in_store"
    # Block 4 — semantic assertions that match step-handler intent.
    # The bones-only assertion set proved indirect facts (artifact
    # exists, ticket is X). The new kinds let scenarios assert the
    # exact piece of state the handler advertises (a contract was
    # written with intent fields, a wireframe linted clean, a build-
    # plan layer transitioned, a risk status moved, a cascade landed
    # with the expected dispositions, etc.).
    CONTRACT_VALIDATED = "contract_validated"
    WIREFRAME = "wireframe"
    BUILD_PLAN_LAYER_STATUS = "build_plan_layer_status"
    RISK_STATUS = "risk_status"
    CASCADE_PROPOSAL = "cascade_proposal"
    ONTOLOGY_TERM = "ontology_term"
    DISCOVERY_STATE_CONSISTENT = "discovery_state_consistent"
    ENVELOPE_UPDATED = "envelope_updated"
    ORPHAN_REPORT = "orphan_report"
    PROVISIONING_SUCCEEDED = "provisioning_succeeded"
    FIXTURE_CASSETTE = "fixture_cassette"
    TIER_PROMOTION = "tier_promotion"


class _AssertionBase(BaseModel):
    """Shared envelope. ``extra='forbid'`` catches typos in YAML."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)


class ArtifactWrittenAssertion(_AssertionBase):
    """A file at ``path`` (project-root-relative) exists.

    Optional ``contains`` substring matches the file's text content;
    useful for "the L0 pitch landed and mentions <keyword>" without
    requiring an exact-match fixture.
    """

    kind: Literal["artifact_written"] = AssertionKind.ARTIFACT_WRITTEN.value
    path: str = Field(..., min_length=1)
    contains: str | None = None


class AnalyticsEventEmittedAssertion(_AssertionBase):
    """At least one analytics event of ``event_kind`` was emitted.

    ``field_constraints`` (optional) narrows to events whose top-level
    fields match all listed key/value pairs. For bones this is a
    surface check — values must be JSON-serializable scalars; deep
    structural matches land with MVP if needed.
    """

    kind: Literal["analytics_event_emitted"] = (
        AssertionKind.ANALYTICS_EVENT_EMITTED.value
    )
    event_kind: str = Field(..., min_length=1)
    field_constraints: dict[str, str | int | float | bool | None] = Field(
        default_factory=dict,
    )


class TicketStatusAssertion(_AssertionBase):
    """A ticket in the store reached ``status``.

    Status values are the string forms of ``jig.ticket.TicketStatus``
    (``open``, ``in_progress``, ``resolved``, etc.). The driver
    resolves them by string compare to avoid coupling the simulator
    schema to the ticket-status enum (which evolves on its own
    cadence).
    """

    kind: Literal["ticket_status"] = AssertionKind.TICKET_STATUS.value
    ticket_id: str = Field(..., min_length=1)
    status: str = Field(..., min_length=1)


class ReviewerReturnedNoCriticalAssertion(_AssertionBase):
    """The given reviewer's last invocation returned no critical comments.

    Bones uses this only against ``contract-compliance``. Other reviewer
    ids land alongside the rest of the federation (Track G6+).
    """

    kind: Literal["reviewer_returned_no_critical"] = (
        AssertionKind.REVIEWER_RETURNED_NO_CRITICAL.value
    )
    reviewer_id: str = Field(..., min_length=1)


class EnvVarSetAssertion(_AssertionBase):
    """One env-var entry from the most recent sim step matches.

    Block 2 covers ``JIG_FIXTURE_MODE`` spawn-env checks and the
    operator-supplied connection string surfacing — both stamp their
    output on the driver context so the assertion can introspect
    without depending on live process environment.

    ``source`` selects which ctx field to read:

    - ``fixture_env`` (default) → ``ctx.last_fixture_env``
    - ``operator_supplied`` → returns the single
      ``{"_url": ctx.last_operator_supplied_url}`` view, so the
      assertion can match the URL with ``name="_url"``.
    """

    kind: Literal["env_var_set"] = AssertionKind.ENV_VAR_SET.value
    name: str = Field(..., min_length=1)
    value: str | None = None
    source: Literal["fixture_env", "operator_supplied"] = "fixture_env"


class ReviewCommentInStoreAssertion(_AssertionBase):
    """A comment from ``reviewer_id`` is in the store for ``ticket_id``.

    Block 3 (federation) — pins the gap the v2-review called out.
    Pre-Block-3 the specialty scenario only verified that a reviewer
    was *selected* (``invoke_specialty_reviewer``); this assertion
    verifies the comment actually landed via spawn-and-post.

    Optional ``contains`` substring matches against any comment's
    ``prose`` (case-sensitive). Optional ``severity`` narrows to
    comments at the requested severity tier.
    """

    kind: Literal["review_comment_in_store"] = (
        AssertionKind.REVIEW_COMMENT_IN_STORE.value
    )
    ticket_id: str = Field(..., min_length=1)
    reviewer_id: str = Field(..., min_length=1)
    contains: str | None = None
    severity: Literal["critical", "important", "notable"] | None = None


class CostUnderBudgetAssertion(_AssertionBase):
    """Total scenario cost stayed under ``usd``.

    Mock-mode runs have no LLM cost so the driver evaluates this as
    ``observed_cost == 0.0 < usd`` — always true. Real-mode runs
    aggregate ``AgentCompleted.cost_estimate_usd`` across the run; the
    bones success criterion is < $1.
    """

    kind: Literal["cost_under_budget"] = AssertionKind.COST_UNDER_BUDGET.value
    usd: float = Field(..., gt=0.0)


# ---- Block 4 — semantic assertions matching step-handler intent ---------
#
# Pre-Block-4 final-track scenarios could only assert indirect facts
# (artifact exists, ticket is in status X). Per the v2 code review's
# important finding #4, the assertion set has lagged the simulator's
# step-kind growth (~32 step kinds vs 5 bones assertion kinds). Each
# new assertion below pins the *semantic* state a step handler
# advertises so a scenario can prove the right thing happened, not just
# that something landed on disk.


class ContractValidatedAssertion(_AssertionBase):
    """A behavioral or data contract exists in the architecture.

    Reads ``.jig/spec/modules/<module_id>/contracts.yaml`` and asserts
    the named contract id is present, optionally verifying its
    ``intent`` fields are populated (intent-compliance reviewer's
    pre-condition). Mirrors the contract-validated facts the SA-MVP
    discovery-loop step writes; lets a scenario prove the contract
    landed with intent rather than just that the YAML grew.
    """

    kind: Literal["contract_validated"] = AssertionKind.CONTRACT_VALIDATED.value
    module_id: str = Field(..., min_length=1)
    contract_id: str = Field(..., min_length=1)
    contract_kind: Literal["behavioral", "data"]
    require_intent: bool = Field(
        default=True,
        description=(
            "When True (default) the contract's ``intent.problem`` and "
            "``intent.simplest_solution`` must be non-empty. Disable for "
            "tests that exercise schema-level optionality."
        ),
    )


class WireframeAssertion(_AssertionBase):
    """A wireframe HTML file exists at the expected screen-derived path.

    Reads ``.jig/spec/wireframes/<screen_id>.html``. Optional
    ``contains`` substring matches the file content; optional
    ``lint_passed`` flag asserts the rendered HTML carries no
    ``<!-- LINT-FAIL: ... -->`` markers (the wireframe-linter's
    convention for inline failures). Lets visual scenarios prove the
    wireframe round-tripped without reaching for ``artifact_written``.
    """

    kind: Literal["wireframe"] = AssertionKind.WIREFRAME.value
    screen_id: str = Field(..., min_length=1)
    contains: str | None = None
    lint_passed: bool = True


class BuildPlanLayerStatusAssertion(_AssertionBase):
    """An epic's layer status in the build plan matches the expected value.

    Reads ``.jig/plan/build-plan.yaml``, looks up ``epic_id``, and
    inspects ``layers.<layer>.status``. Promotes the previously-
    implicit "layer-transitioned" check to a first-class assertion.
    """

    kind: Literal["build_plan_layer_status"] = (
        AssertionKind.BUILD_PLAN_LAYER_STATUS.value
    )
    epic_id: str = Field(..., min_length=1)
    layer: Literal["bones", "mvp", "final"]
    status: Literal["not_started", "in_progress", "blocked", "done"]


class RiskStatusAssertion(_AssertionBase):
    """A risk in the architecture has the expected status.

    Reads ``.jig/spec/architecture.yaml`` and finds the risk by id.
    The risk-state machine is the public-facing contract for the
    spike workflow — ``open`` → ``spike_proposed`` →
    ``spike_running`` → ``mitigated`` / ``confirmed_impossible`` /
    ``mitigated_with_constraints`` — and many scenarios advance it.
    Until now they could only assert the analytics event fired.
    """

    kind: Literal["risk_status"] = AssertionKind.RISK_STATUS.value
    risk_id: str = Field(..., min_length=1)
    status: Literal[
        "open",
        "spike_proposed",
        "spike_running",
        "mitigated",
        "mitigated_with_constraints",
        "accepted",
        "confirmed_impossible",
    ]


class CascadeProposalAssertion(_AssertionBase):
    """A cascade proposal exists for ``risk_id`` with expected dispositions.

    Globs ``.jig/arch/cascades/<risk_id>-*.yaml`` (the writer-stamped
    timestamp suffix means we can't pin the exact path without
    coordination), validates the most-recent proposal, and asserts
    its top-level ``state`` plus the per-disposition tally in
    ``contracts``. Pre-Block-4 the cascade scenarios could only
    confirm the file exists; this assertion proves the contents.
    """

    kind: Literal["cascade_proposal"] = AssertionKind.CASCADE_PROPOSAL.value
    risk_id: str = Field(..., min_length=1)
    state: Literal["pending", "staged", "holding", "rejected", "resolved"] | None = None
    min_contracts: int = Field(
        default=0,
        ge=0,
        description=(
            "Minimum number of CascadeContractDisposition rows the "
            "proposal must carry. 0 (default) skips the count check."
        ),
    )
    contains_disposition: (
        Literal["invalidated", "needs_revision", "still_holds"] | None
    ) = Field(
        default=None,
        description=(
            "When set, at least one row in ``contracts`` must carry "
            "this disposition value."
        ),
    )


class OntologyTermAssertion(_AssertionBase):
    """An ontology term exists with the expected definition substring.

    Loads ``.jig/spec/ontology.md``, looks up the term (case-
    insensitive), and asserts that ``definition_contains`` appears in
    the rendered definition. Lets the L1-PO ontology scenarios prove
    the term landed with content rather than just that the file grew.
    """

    kind: Literal["ontology_term"] = AssertionKind.ONTOLOGY_TERM.value
    term: str = Field(..., min_length=1)
    definition_contains: str | None = None


class DiscoveryStateConsistentAssertion(_AssertionBase):
    """L1 discovery state is consistent with discovery.md.

    Runs ``validate_state_consistency`` and asserts the divergence
    count matches ``expected_divergence_count`` (default 0 — fully
    consistent). Lets resume scenarios prove the reconcile happened
    without inspecting the divergence list directly.
    """

    kind: Literal["discovery_state_consistent"] = (
        AssertionKind.DISCOVERY_STATE_CONSISTENT.value
    )
    expected_divergence_count: int = Field(default=0, ge=0)


class EnvelopeUpdatedAssertion(_AssertionBase):
    """The estimation envelope for ``size`` has shifted from the default.

    Reads the calibration store, computes the envelope for ``size``,
    and asserts ``sample_count >= min_sample_count`` (i.e. the
    operator-feedback loop deposited at least N samples). Lets the
    quartermaster-feedback scenario prove the envelope moved rather
    than just that the analytics event fired.
    """

    kind: Literal["envelope_updated"] = AssertionKind.ENVELOPE_UPDATED.value
    size: Literal["xs", "s", "m", "l", "xl"]
    min_sample_count: int = Field(default=1, ge=1)


class OrphanReportAssertion(_AssertionBase):
    """The orphan tracker categorized the expected number of namespaces.

    Reads ``.jig/dev/orphans.jsonl`` and asserts the latest report's
    counts match the expected breakdown. Bones-only scenarios checked
    artifact_written; this assertion verifies the categorization
    output instead.
    """

    kind: Literal["orphan_report"] = AssertionKind.ORPHAN_REPORT.value
    min_entries: int = Field(default=1, ge=0)


class ProvisioningSucceededAssertion(_AssertionBase):
    """A service was provisioned and its connection string surfaced.

    Asserts the named service id appears in
    ``ctx.dev_provisioning_env_vars`` (or one of the per-service
    captured-env maps the dev-provisioning step stamps). Pre-Block-4
    scenarios checked artifact_written on a manifest file; this
    assertion verifies the provisioner actually returned a URL.
    """

    kind: Literal["provisioning_succeeded"] = AssertionKind.PROVISIONING_SUCCEEDED.value
    service_id: str = Field(..., min_length=1)
    url_contains: str | None = Field(
        default=None,
        description=(
            "Optional substring the provisioner-supplied URL must "
            "contain. Useful for verifying namespace templating."
        ),
    )


class FixtureCassetteAssertion(_AssertionBase):
    """A fixture cassette exists for ``service_id`` with the expected signature.

    Reads ``.jig/dev/fixtures/<service_id>.jsonl`` and asserts at
    least one cassette matches ``request_signature``. Lets the
    fixture-replay scenarios prove the cassette landed with the
    operator-expected request shape.
    """

    kind: Literal["fixture_cassette"] = AssertionKind.FIXTURE_CASSETTE.value
    service_id: str = Field(..., min_length=1)
    request_signature: str | None = None


class TierPromotionAssertion(_AssertionBase):
    """A ticket got tier-promoted from one rung to another.

    Reads ``ctx.last_tier_promotion_*`` (stamped by the
    invoke_tier_promotion handler). When ``from_tier`` and
    ``to_tier`` are set, both must match; passing only ``to_tier``
    lets the assertion verify "ended at this tier" without coupling
    to the starting rung.
    """

    kind: Literal["tier_promotion"] = AssertionKind.TIER_PROMOTION.value
    from_tier: Literal["standard", "senior", "sa"] | None = None
    to_tier: Literal["standard", "senior", "sa"]


# Discriminated union; the YAML loader passes raw dicts to
# ``validate_python``. ``TypeAdapter`` mirrors the parse_event pattern in
# ``jig.analytics.events`` without paying for a wrapper BaseModel.
ScenarioAssertionUnion = Annotated[
    Union[
        ArtifactWrittenAssertion,
        AnalyticsEventEmittedAssertion,
        TicketStatusAssertion,
        ReviewerReturnedNoCriticalAssertion,
        CostUnderBudgetAssertion,
        EnvVarSetAssertion,
        ReviewCommentInStoreAssertion,
        # Block 4 — semantic assertions matching step-handler intent.
        ContractValidatedAssertion,
        WireframeAssertion,
        BuildPlanLayerStatusAssertion,
        RiskStatusAssertion,
        CascadeProposalAssertion,
        OntologyTermAssertion,
        DiscoveryStateConsistentAssertion,
        EnvelopeUpdatedAssertion,
        OrphanReportAssertion,
        ProvisioningSucceededAssertion,
        FixtureCassetteAssertion,
        TierPromotionAssertion,
    ],
    Field(discriminator="kind"),
]

ScenarioAssertion: TypeAdapter[ScenarioAssertionUnion] = TypeAdapter(
    ScenarioAssertionUnion,
)
