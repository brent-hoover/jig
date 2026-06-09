"""Tests for v2 SA schemas — Architecture, Module, ContractsFile, Risk."""
from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from jig.intent import ComplicationsConsidered, Intent
from jig.schemas.arch import (
    Architecture,
    BehavioralContract,
    BoundariesFile,
    CascadeContractDisposition,
    CascadeProposal,
    CascadeStage,
    CascadeState,
    ChangeLogEntry,
    ContractsFile,
    DataContract,
    DataStore,
    DevProvisioning,
    ExternalBoundaries,
    ExternalDependency,
    InternalBoundaries,
    IntegrationAcceptance,
    Module,
    OntologyTerm,
    OwnedCollection,
    Risk,
    RiskImpact,
    RiskLikelihood,
    RiskStatus,
    SharedContract,
    SourceType,
    TechDecision,
    TierHint,
)


def _intent(problem: str = "P", simplest: str = "S") -> Intent:
    return Intent(problem=problem, simplest_solution=simplest)


# ---------------------------------------------------------------------------
# Architecture top-level
# ---------------------------------------------------------------------------


def test_empty_architecture_valid():
    a = Architecture()
    assert a.spec_version == 1
    assert a.modules == []


# ---------------------------------------------------------------------------
# TechDecision (SA grounded-decision record)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("source_type, source_ref", [
    (SourceType.context7, "/typer/latest"),
    (SourceType.live_fetch,
     "https://hacker-news.firebaseio.com/v0/item/8863.json"),
    (SourceType.operator_specified, None),
    (SourceType.inferred, None),
])
def test_tech_decision_valid_for_each_source_type(source_type, source_ref):
    td = TechDecision(
        id="cli-framework",
        choice="typer",
        rationale="Declarative subcommand parsing.",
        source_type=source_type,
        source_ref=source_ref,
        version_pinned="0.12" if source_type is SourceType.context7 else None,
    )
    assert td.source_type is source_type
    assert td.source_ref == source_ref


def test_tech_decision_rejects_extra_field():
    with pytest.raises(ValidationError, match="extra"):
        TechDecision(
            id="cli-framework",
            choice="typer",
            rationale="x",
            source_type=SourceType.inferred,
            bogus="nope",
        )


def test_tech_decision_rejects_non_kebab_id():
    with pytest.raises(ValidationError, match="TechDecision.id"):
        TechDecision(
            id="CLI Framework",
            choice="typer",
            rationale="x",
            source_type=SourceType.inferred,
        )


@pytest.mark.parametrize("source_type", [SourceType.context7, SourceType.live_fetch])
def test_tech_decision_requires_source_ref_when_grounded(source_type):
    with pytest.raises(ValidationError, match="source_ref is required"):
        TechDecision(
            id="cli-framework",
            choice="typer",
            rationale="x",
            source_type=source_type,
            source_ref=None,
        )


def test_tech_decision_rejects_blank_source_ref_when_grounded():
    with pytest.raises(ValidationError, match="source_ref is required"):
        TechDecision(
            id="cli-framework",
            choice="typer",
            rationale="x",
            source_type=SourceType.context7,
            source_ref="   ",
        )


def test_architecture_defaults_tech_decisions_empty():
    a = Architecture()
    assert a.tech_decisions == []


def test_architecture_size_defaults_and_validates():
    assert Architecture().size == "S"
    assert Architecture(size="M").size == "M"
    assert Architecture(size="L").size == "L"  # forward-positioned for Phase 2
    with pytest.raises(ValidationError, match="size"):
        Architecture(size="XL")


def test_architecture_rejects_duplicate_tech_decision_ids():
    with pytest.raises(ValidationError, match="tech_decisions"):
        Architecture(
            tech_decisions=[
                TechDecision(id="cli-framework", choice="typer", rationale="x",
                             source_type=SourceType.inferred),
                TechDecision(id="cli-framework", choice="click", rationale="y",
                             source_type=SourceType.inferred),
            ],
        )


def test_architecture_parses_with_tech_decisions():
    a = Architecture(
        tech_decisions=[TechDecision(
            id="http-client",
            choice="httpx",
            rationale="async/sync dual support.",
            source_type=SourceType.context7,
            source_ref="/encode/httpx",
            version_pinned="0.27",
        )],
    )
    assert len(a.tech_decisions) == 1
    assert a.tech_decisions[0].choice == "httpx"


def test_architecture_with_module_and_data_store():
    a = Architecture(
        data_stores=[DataStore(id="main-db", kind="postgres",
                               accessed_by=["catalog-ingest"])],
        modules=[Module(
            id="catalog-ingest",
            title="Catalog Ingest",
            summary="pulls from customer systems, normalizes",
            implements_capabilities=["shopify-connect", "normalize-skus"],
            owns=["products"],
            tier_hint=TierHint.SENIOR,
            requires_tracer_bullet=True,
            intent=_intent("ingest customer catalogs", "single function per source"),
        )],
        change_log=[ChangeLogEntry(revision=1, date=date(2026, 4, 30),
                                   summary="initial pass")],
    )
    assert a.modules[0].tier_hint == TierHint.SENIOR
    assert a.modules[0].requires_tracer_bullet is True


def test_module_requires_intent():
    with pytest.raises(ValidationError, match="intent"):
        Module(
            id="m",
            title="t",
            summary="s",
        )


def test_shared_contract_event_shape():
    sc = SharedContract(
        id="catalog-ingested-event",
        type="event",
        publisher="catalog-ingest",
        subscribers=["categorization"],
        payload_ref="project://arch/contracts/shared/catalog-ingested",
    )
    assert sc.subscribers == ["categorization"]


# ---------------------------------------------------------------------------
# Risks
# ---------------------------------------------------------------------------


def test_risk_open_status_no_dependent_contracts_required():
    r = Risk(
        id="r-multi-tenancy",
        text="single-instance vs per-customer",
        impact=RiskImpact.HIGH,
        likelihood=RiskLikelihood.HIGH,
        status=RiskStatus.OPEN,
    )
    assert r.dependent_contracts == []
    assert r.intent is None


def test_risk_with_intent_and_dependent_contracts():
    r = Risk(
        id="r-shopify-delta",
        text="unclear if Shopify supports clean delta",
        impact=RiskImpact.MEDIUM,
        likelihood=RiskLikelihood.MEDIUM,
        status=RiskStatus.SPIKE_PROPOSED,
        spike_ticket="spike-shopify-delta",
        dependent_contracts=[
            "project://arch/modules/catalog-ingest/contracts#external_dependencies/shopify-api",
        ],
        cascade_breaking_likely=True,
        intent=_intent(),
    )
    assert r.cascade_breaking_likely is True
    assert len(r.dependent_contracts) == 1


# ---------------------------------------------------------------------------
# Per-module contracts file
# ---------------------------------------------------------------------------


def test_contracts_file_minimum_valid():
    cf = ContractsFile(module="catalog-ingest")
    assert cf.spec_version == 1
    assert cf.module == "catalog-ingest"
    assert cf.owns == []


def test_contracts_file_with_full_shape():
    cf = ContractsFile(
        module="catalog-ingest",
        owns=[OwnedCollection(collection="products", db="main-db",
                              read_access=["categorization"])],
        external_dependencies=[ExternalDependency(
            id="shopify-api", kind="external_http",
            rate_limit="2 req/sec per shop",
            failure_mode="retry with exponential backoff",
        )],
        integration_ac=[IntegrationAcceptance(
            capability="shopify-connect",
            must=[
                "OAuth tokens stored encrypted",
                "Rate limit honored",
            ],
        )],
        behavioral_contracts=[BehavioralContract(
            id="ingest-batch-atomicity",
            applies_to={"capability": "normalize-skus", "module": "catalog-ingest"},
            precondition="batch_id refers to in-progress row",
            postcondition="all-or-nothing persistence",
            invariant="status transitions are forward-only",
            side_effects=["Writes to products"],
            intent=_intent("partial batches break consumers", "single transaction"),
        )],
        data_contracts=[DataContract(
            id="product-shape", description="normalized product",
            schema_ref="project://arch/contracts/shared/product",
            intent=_intent("uniform shape across modules", "Pydantic model"),
        )],
    )
    assert cf.behavioral_contracts[0].postcondition == "all-or-nothing persistence"
    assert cf.data_contracts[0].id == "product-shape"


def test_behavioral_contract_requires_intent():
    with pytest.raises(ValidationError, match="intent"):
        BehavioralContract(
            id="c",
            postcondition="x",
        )


def test_data_contract_requires_intent():
    with pytest.raises(ValidationError, match="intent"):
        DataContract(id="c", description="x")


def test_owned_collection_defaults_self_write_access():
    o = OwnedCollection(collection="products", db="main-db")
    assert o.write_access == ["self"]
    assert o.read_access == []


# ---------------------------------------------------------------------------
# Track E MVP — DevProvisioning + DataStore.dev_provisioning
# ---------------------------------------------------------------------------


def test_dev_provisioning_defaults_round_trip():
    p = DevProvisioning(strategy="shared_namespaced")
    assert p.namespace_template == "agent_{agent_id}_{ticket_id}"
    assert p.cleanup_on_success == "drop"
    assert p.cleanup_on_failure == "archive"
    assert p.connection_string_template == ""
    blob = p.model_dump()
    p2 = DevProvisioning.model_validate(blob)
    assert p2 == p


def test_dev_provisioning_full_shape_round_trip():
    p = DevProvisioning(
        strategy="shared_namespaced",
        namespace_template="agent_{ticket_id}",
        cleanup_on_success="archive",
        cleanup_on_failure="drop",
        connection_string_template=(
            "postgresql://jig:jig@localhost:5432/jigdev"
            "?options=-c%20search_path%3D{namespace}"
        ),
    )
    blob = p.model_dump()
    p2 = DevProvisioning.model_validate(blob)
    assert p2 == p


def test_dev_provisioning_per_agent_ephemeral_parses_as_stub():
    """MVP-stub strategies validate but no provisioner is wired yet."""
    p = DevProvisioning(strategy="per_agent_ephemeral")
    assert p.strategy == "per_agent_ephemeral"


def test_dev_provisioning_operator_supplied_parses_as_stub():
    p = DevProvisioning(strategy="operator_supplied")
    assert p.strategy == "operator_supplied"


def test_dev_provisioning_rejects_unknown_strategy():
    with pytest.raises(ValidationError, match="strategy"):
        DevProvisioning(strategy="nonsense")  # type: ignore[arg-type]


def test_dev_provisioning_rejects_unknown_cleanup_policy():
    with pytest.raises(ValidationError, match="cleanup_on_success"):
        DevProvisioning(
            strategy="shared_namespaced",
            cleanup_on_success="nuke",  # type: ignore[arg-type]
        )


def test_dev_provisioning_forbids_extra_keys():
    with pytest.raises(ValidationError, match="extra"):
        DevProvisioning(strategy="shared_namespaced", surprise="boom")  # type: ignore[call-arg]


def test_data_store_dev_provisioning_default_is_none():
    ds = DataStore(id="main-db", kind="postgres")
    assert ds.dev_provisioning is None


def test_data_store_with_dev_provisioning_round_trip():
    ds = DataStore(
        id="main-db",
        kind="postgres",
        accessed_by=["catalog-ingest"],
        dev_provisioning=DevProvisioning(
            strategy="shared_namespaced",
            namespace_template="agent_{ticket_id}",
            connection_string_template=(
                "postgresql://jig:jig@localhost:5432/jigdev"
                "?options=-c%20search_path%3D{namespace}"
            ),
        ),
    )
    blob = ds.model_dump()
    ds2 = DataStore.model_validate(blob)
    assert ds2 == ds
    assert ds2.dev_provisioning is not None
    assert ds2.dev_provisioning.strategy == "shared_namespaced"


def test_architecture_with_data_store_dev_provisioning():
    a = Architecture(
        data_stores=[
            DataStore(
                id="main-db",
                kind="postgres",
                dev_provisioning=DevProvisioning(strategy="shared_namespaced"),
            ),
        ],
    )
    blob = a.model_dump(mode="json")
    a2 = Architecture.model_validate(blob)
    assert a2.data_stores[0].dev_provisioning is not None


def test_intent_with_complications_round_trips_through_contract():
    """The complications_considered extra-keys passthrough survives nesting."""
    bc = BehavioralContract(
        id="c",
        invariant="x",
        intent=Intent(
            problem="P",
            simplest_solution="S",
            complications_considered=ComplicationsConsidered(
                scale="x",
                idempotency="y",  # extra key
            ),
        ),
    )
    blob = bc.model_dump()
    assert blob["intent"]["complications_considered"]["idempotency"] == "y"
    bc2 = BehavioralContract.model_validate(blob)
    assert bc2 == bc


# ---------------------------------------------------------------------------
# Block 4 — non-tautological rejection + cross-field tests (issue #7)
# ---------------------------------------------------------------------------
#
# Existing tests above include several round-trip-only checks (DataStore
# with dev_provisioning, Architecture with dev_provisioning, Intent with
# complications). The reviewer's concern: those don't prove the schema
# rejects bad inputs or enforces cross-field semantics. The tests below
# pin those gaps without removing the smoke checks.


# ---- DataStore / Architecture: missing required + bad enum ----------------


def test_data_store_rejects_missing_kind():
    with pytest.raises(ValidationError, match="kind"):
        DataStore(id="main-db")  # type: ignore[call-arg]


def test_data_store_rejects_extra_field():
    """``extra='forbid'`` chain: typo'd keys must surface, not silently drop."""
    with pytest.raises(ValidationError, match="extra"):
        DataStore(id="main-db", kind="postgres", region="us-east-1")  # type: ignore[call-arg]


def test_module_rejects_naive_generated_at_via_architecture():
    """Architecture.generated_at uses validate_tz_aware — naive must error."""
    from datetime import datetime as _dt

    with pytest.raises(ValidationError, match="timezone"):
        Architecture(generated_at=_dt(2026, 5, 1))


def test_pydantic_extra_forbid_rejects_unknown_keys_on_module():
    with pytest.raises(ValidationError, match="extra"):
        Module(  # type: ignore[call-arg]
            id="catalog-ingest",
            title="t",
            summary="s",
            intent=_intent(),
            owner="ada",  # not a known Module field
        )


# ---- Risk: cross-field semantics on dependent_contracts -------------------
#
# The Risk schema documents that ``dependent_contracts`` is "Required once
# status >= spike_proposed". The current schema implementation enforces
# the URI shape on entries but not the presence rule. We pin BOTH halves
# here so the contract is visible regardless of where in the pipeline the
# enforcement lives:
#
# - happy path: with status=open, dependent_contracts may be empty
# - with status=spike_proposed and well-formed URI list, accept
# - with status=spike_proposed and bad URI in the list, reject (already
#   covered by validate_project_uri_shape but pinning here documents the
#   joint claim).


def test_risk_open_status_allows_empty_dependent_contracts():
    """Smoke check on the documented "open status doesn't require URIs" rule."""
    r = Risk(
        id="r-x",
        text="x",
        impact=RiskImpact.LOW,
        likelihood=RiskLikelihood.LOW,
        status=RiskStatus.OPEN,
    )
    assert r.dependent_contracts == []


def test_risk_spike_proposed_with_well_formed_uri_accepted():
    r = Risk(
        id="r-x",
        text="x",
        impact=RiskImpact.MEDIUM,
        likelihood=RiskLikelihood.MEDIUM,
        status=RiskStatus.SPIKE_PROPOSED,
        dependent_contracts=[
            "project://arch/modules/m/contracts#owns/products",
        ],
        intent=_intent(),
    )
    assert len(r.dependent_contracts) == 1


def test_risk_spike_proposed_with_bad_uri_rejected():
    """URI-shape validator fires inside the list, not just the field."""
    with pytest.raises(ValidationError, match="prefix"):
        Risk(
            id="r-x",
            text="x",
            impact=RiskImpact.MEDIUM,
            likelihood=RiskLikelihood.MEDIUM,
            status=RiskStatus.SPIKE_PROPOSED,
            dependent_contracts=["bad-uri"],
            intent=_intent(),
        )


# ---- Architecture: cross-field — non-duplicate module ids -----------------
#
# Block A.2 hoists module-id uniqueness from the SA finalize pass to
# the schema layer. Downstream consumers (PM Coordinator, reviewer
# federation, MCP handlers) all key off ``Module.id`` — duplicates
# silently route work to the wrong entry, so the schema rejects the
# YAML at load time instead of letting the breakage surface inside
# a reviewer.


def test_architecture_rejects_duplicate_module_ids():
    """Schema-level uniqueness gate per Block A.2."""
    with pytest.raises(ValidationError, match="duplicate id"):
        Architecture(
            modules=[
                Module(id="m", title="t", summary="s", intent=_intent()),
                Module(id="m", title="t2", summary="s2", intent=_intent()),
            ]
        )


# ---- ContractsFile: rejection coverage ------------------------------------


def test_contracts_file_rejects_non_kebab_module():
    with pytest.raises(ValidationError, match="kebab"):
        ContractsFile(module="Catalog_Ingest")


def test_contracts_file_rejects_missing_module():
    with pytest.raises(ValidationError, match="module"):
        ContractsFile()  # type: ignore[call-arg]


def test_pydantic_extra_forbid_rejects_unknown_keys_on_contracts_file():
    with pytest.raises(ValidationError, match="extra"):
        ContractsFile(module="catalog-ingest", surprise="boom")  # type: ignore[call-arg]


# ---- BehavioralContract / DataContract: missing-required ------------------


def test_behavioral_contract_rejects_missing_id():
    with pytest.raises(ValidationError, match="id"):
        BehavioralContract(invariant="x", intent=_intent())  # type: ignore[call-arg]


def test_data_contract_rejects_missing_id():
    with pytest.raises(ValidationError, match="id"):
        DataContract(description="x", intent=_intent())  # type: ignore[call-arg]


# ---- ChangeLogEntry: revision must be >= 1 --------------------------------


def test_change_log_entry_rejects_revision_zero():
    with pytest.raises(ValidationError, match="revision"):
        ChangeLogEntry(revision=0, date=date(2026, 5, 1), summary="x")


def test_change_log_entry_rejects_negative_revision():
    with pytest.raises(ValidationError, match="revision"):
        ChangeLogEntry(revision=-1, date=date(2026, 5, 1), summary="x")


# ---- Risk: schema-level conditional invariants ----------------------------
#
# Before Block A.1 the cascade-prep gates lived in ``jig.sa_incremental_mcp``
# and only fired on the upsert path; constructing a Risk directly let an
# invalid combination land in the architecture. The schema-level
# ``model_validator`` mirrors the same rule on every construction path.


_VALID_DEP_URI = "project://arch/modules/m/contracts#owns/products"


def _spike_proposed_kwargs() -> dict:
    return dict(
        id="r-x",
        text="x",
        impact=RiskImpact.MEDIUM,
        likelihood=RiskLikelihood.MEDIUM,
        status=RiskStatus.SPIKE_PROPOSED,
        dependent_contracts=[_VALID_DEP_URI],
        intent=_intent(),
    )


def test_risk_spike_proposed_happy_path():
    r = Risk(**_spike_proposed_kwargs())
    assert r.dependent_contracts == [_VALID_DEP_URI]
    assert r.intent is not None


@pytest.mark.parametrize(
    "status",
    [
        RiskStatus.SPIKE_PROPOSED,
        RiskStatus.SPIKE_RUNNING,
        RiskStatus.MITIGATED,
        RiskStatus.MITIGATED_WITH_CONSTRAINTS,
        RiskStatus.ACCEPTED,
        RiskStatus.CONFIRMED_IMPOSSIBLE,
    ],
)
def test_risk_post_open_status_requires_dependent_contracts(status):
    kwargs = _spike_proposed_kwargs() | {
        "status": status,
        "dependent_contracts": [],
    }
    with pytest.raises(ValidationError, match="dependent_contracts"):
        Risk(**kwargs)


@pytest.mark.parametrize(
    "status",
    [
        RiskStatus.SPIKE_PROPOSED,
        RiskStatus.SPIKE_RUNNING,
        RiskStatus.MITIGATED,
        RiskStatus.MITIGATED_WITH_CONSTRAINTS,
        RiskStatus.ACCEPTED,
        RiskStatus.CONFIRMED_IMPOSSIBLE,
    ],
)
def test_risk_post_open_status_requires_intent(status):
    kwargs = _spike_proposed_kwargs() | {"status": status, "intent": None}
    with pytest.raises(ValidationError, match="intent"):
        Risk(**kwargs)


def test_risk_open_allows_no_dependents_and_no_intent():
    """``OPEN`` is the early-capture state — both gates must stay off."""
    r = Risk(
        id="r-open",
        text="x",
        impact=RiskImpact.LOW,
        likelihood=RiskLikelihood.LOW,
        status=RiskStatus.OPEN,
    )
    assert r.dependent_contracts == []
    assert r.intent is None


# ---- Module: cascade_risk_low_rationale conditional invariant ------------
#
# When the SA flips the ``cascade_risk_low`` hint to True, the rationale
# must be substantive enough for the PM coordinator (and audit trail)
# to read; a bare boolean toggle defeats that purpose. The minimum
# floor (>= 10 chars after strip) keeps "ok" / "n/a" out of the artifact.


def test_module_cascade_risk_low_false_does_not_require_rationale():
    m = Module(
        id="m",
        title="t",
        summary="s",
        intent=_intent(),
    )
    assert m.cascade_risk_low is False
    assert m.cascade_risk_low_rationale is None


def test_module_cascade_risk_low_true_with_rationale_accepted():
    m = Module(
        id="m",
        title="t",
        summary="s",
        intent=_intent(),
        cascade_risk_low=True,
        cascade_risk_low_rationale=(
            "no shared shapes, no new contracts, internal-only"
        ),
    )
    assert m.cascade_risk_low is True


def test_module_cascade_risk_low_true_without_rationale_rejected():
    with pytest.raises(ValidationError, match="cascade_risk_low_rationale"):
        Module(
            id="m",
            title="t",
            summary="s",
            intent=_intent(),
            cascade_risk_low=True,
        )


def test_module_cascade_risk_low_true_with_short_rationale_rejected():
    """Single-token / "ok" prose defeats the audit trail's purpose."""
    with pytest.raises(ValidationError, match="cascade_risk_low_rationale"):
        Module(
            id="m",
            title="t",
            summary="s",
            intent=_intent(),
            cascade_risk_low=True,
            cascade_risk_low_rationale="ok",
        )


def test_module_cascade_risk_low_true_with_whitespace_rationale_rejected():
    """The 10-char floor applies after stripping leading/trailing ws."""
    with pytest.raises(ValidationError, match="cascade_risk_low_rationale"):
        Module(
            id="m",
            title="t",
            summary="s",
            intent=_intent(),
            cascade_risk_low=True,
            cascade_risk_low_rationale="    ok    ",
        )


# ---- BehavioralContract: must constrain at least one thing ----------------


def test_behavioral_contract_with_postcondition_accepted():
    bc = BehavioralContract(
        id="bc-x",
        applies_to={"module": "m"},
        postcondition="row exists with the right tenant",
        intent=_intent(),
    )
    assert bc.postcondition is not None


def test_behavioral_contract_with_invariant_accepted():
    bc = BehavioralContract(
        id="bc-x",
        scope="cross_cutting",
        invariant="status transitions are forward-only",
        intent=_intent(),
    )
    assert bc.invariant is not None


def test_behavioral_contract_with_only_side_effects_accepted():
    bc = BehavioralContract(
        id="bc-x",
        scope="cross_cutting",
        side_effects=["audit_log appended"],
        intent=_intent(),
    )
    assert bc.side_effects == ["audit_log appended"]


def test_behavioral_contract_with_only_side_effect_required_accepted():
    bc = BehavioralContract(
        id="bc-x",
        scope="cross_cutting",
        side_effect_required="audit row created",
        intent=_intent(),
    )
    assert bc.side_effect_required is not None


def test_behavioral_contract_with_only_precondition_accepted():
    bc = BehavioralContract(
        id="bc-x",
        applies_to={"module": "m"},
        precondition="incoming row has canonical shape",
        intent=_intent(),
    )
    assert bc.precondition is not None


def test_behavioral_contract_with_no_constraints_rejected():
    """A behavioral contract that constrains nothing is meaningless."""
    with pytest.raises(ValidationError, match="constrain"):
        BehavioralContract(
            id="bc-empty",
            applies_to={"module": "m"},
            intent=_intent(),
        )


# ---- DataContract: must declare a shape ----------------------------------


def test_data_contract_with_schema_ref_accepted():
    dc = DataContract(
        id="dc-x",
        schema_ref="project://arch/contracts/shared/x",
        intent=_intent(),
    )
    assert dc.schema_ref is not None


def test_data_contract_with_fields_accepted():
    dc = DataContract(
        id="dc-x",
        fields={"id": "str", "name": "str"},
        intent=_intent(),
    )
    assert dc.fields is not None


def test_data_contract_with_neither_schema_ref_nor_fields_rejected():
    with pytest.raises(ValidationError, match="schema_ref or fields"):
        DataContract(id="dc-empty", intent=_intent())


def test_data_contract_with_empty_fields_dict_and_no_schema_ref_rejected():
    """Empty fields dict is functionally absent — must reject."""
    with pytest.raises(ValidationError, match="schema_ref or fields"):
        DataContract(id="dc-empty", fields={}, intent=_intent())


# ---- CascadeProposal: state-driven required fields -----------------------


def _cascade_kwargs(**overrides) -> dict:
    base = dict(
        cascade_id="r-x-2026-05-01t00-00-00",
        risk_id="r-x",
        spike_ticket_id="spike-x",
        finding="x",
        contracts=[
            CascadeContractDisposition(
                uri="project://arch/modules/m/contracts#owns/x",
                proposed_disposition="still_holds",
            )
        ],
    )
    base.update(overrides)
    return base


def test_cascade_proposal_pending_state_default_accepted():
    cp = CascadeProposal(**_cascade_kwargs())
    assert cp.state == CascadeState.PENDING


def test_cascade_proposal_holding_state_requires_holding_for():
    with pytest.raises(ValidationError, match="holding_for"):
        CascadeProposal(**_cascade_kwargs(state=CascadeState.HOLDING))


def test_cascade_proposal_holding_state_with_holding_for_accepted():
    cp = CascadeProposal(
        **_cascade_kwargs(
            state=CascadeState.HOLDING,
            holding_for="r-other-2026-01-01t00-00-00",
        )
    )
    assert cp.holding_for is not None


def test_cascade_proposal_rejected_state_requires_rejected_reason():
    with pytest.raises(ValidationError, match="rejected_reason"):
        CascadeProposal(**_cascade_kwargs(state=CascadeState.REJECTED))


def test_cascade_proposal_rejected_state_with_reason_accepted():
    cp = CascadeProposal(
        **_cascade_kwargs(
            state=CascadeState.REJECTED,
            rejected_reason="operator override after architectural review",
        )
    )
    assert cp.rejected_reason is not None


def test_cascade_proposal_staged_state_requires_stages():
    with pytest.raises(ValidationError, match="stages"):
        CascadeProposal(**_cascade_kwargs(state=CascadeState.STAGED))


def test_cascade_proposal_staged_state_with_stages_accepted():
    cp = CascadeProposal(
        **_cascade_kwargs(
            state=CascadeState.STAGED,
            stages=[
                CascadeStage(
                    stage_id="r-x-2026-05-01t00-00-00-stage-1",
                    contracts=[
                        CascadeContractDisposition(
                            uri="project://arch/modules/m/contracts#owns/x",
                            proposed_disposition="still_holds",
                        )
                    ],
                )
            ],
        )
    )
    assert cp.stages != []


def test_cascade_proposal_resolved_state_no_extra_fields_required():
    """``resolved`` doesn't carry an extra-field gate by itself; the
    workflow only reaches it after every stage approves, which the
    stage-approve handler enforces. The schema allows resolved with
    no extra fields so the round-trip from the file works.
    """
    cp = CascadeProposal(
        **_cascade_kwargs(state=CascadeState.RESOLVED)
    )
    assert cp.state == CascadeState.RESOLVED


# ---- Architecture: id-uniqueness across every collection ----------------


def test_architecture_rejects_duplicate_data_store_ids():
    with pytest.raises(ValidationError, match="duplicate id"):
        Architecture(
            data_stores=[
                DataStore(id="db", kind="postgres"),
                DataStore(id="db", kind="sqlite"),
            ]
        )


def test_architecture_rejects_duplicate_shared_contract_ids():
    with pytest.raises(ValidationError, match="duplicate id"):
        Architecture(
            shared_contracts=[
                SharedContract(id="sc", type="data"),
                SharedContract(id="sc", type="event"),
            ]
        )


def test_architecture_rejects_duplicate_risk_ids():
    valid_uri = "project://arch/modules/m/contracts#owns/x"
    with pytest.raises(ValidationError, match="duplicate id"):
        Architecture(
            risks=[
                Risk(
                    id="r1", text="x",
                    impact=RiskImpact.LOW,
                    likelihood=RiskLikelihood.LOW,
                    status=RiskStatus.SPIKE_PROPOSED,
                    dependent_contracts=[valid_uri],
                    intent=_intent(),
                ),
                Risk(
                    id="r1", text="y",
                    impact=RiskImpact.MEDIUM,
                    likelihood=RiskLikelihood.MEDIUM,
                    status=RiskStatus.SPIKE_PROPOSED,
                    dependent_contracts=[valid_uri],
                    intent=_intent(),
                ),
            ]
        )


def test_architecture_rejects_duplicate_open_question_ids():
    from jig.schemas.arch import OpenQuestion as OQ
    with pytest.raises(ValidationError, match="duplicate id"):
        Architecture(
            open_questions=[
                OQ(id="q1", text="x"),
                OQ(id="q1", text="y"),
            ]
        )


def test_architecture_accepts_unique_ids_across_every_collection():
    """Happy path — distinct ids in every collection."""
    a = Architecture(
        data_stores=[DataStore(id="db", kind="postgres")],
        modules=[
            Module(id="m1", title="t", summary="s", intent=_intent()),
            Module(id="m2", title="t", summary="s", intent=_intent()),
        ],
        shared_contracts=[SharedContract(id="sc", type="data")],
    )
    assert len(a.modules) == 2


# ---- ContractsFile: id-uniqueness across every collection ---------------


def test_contracts_file_rejects_duplicate_behavioral_contract_ids():
    with pytest.raises(ValidationError, match="duplicate id"):
        ContractsFile(
            module="m",
            behavioral_contracts=[
                BehavioralContract(
                    id="bc", postcondition="x", intent=_intent(),
                ),
                BehavioralContract(
                    id="bc", postcondition="y", intent=_intent(),
                ),
            ],
        )


def test_contracts_file_rejects_duplicate_data_contract_ids():
    with pytest.raises(ValidationError, match="duplicate id"):
        ContractsFile(
            module="m",
            data_contracts=[
                DataContract(
                    id="dc",
                    schema_ref="project://arch/contracts/shared/x",
                    intent=_intent(),
                ),
                DataContract(
                    id="dc",
                    schema_ref="project://arch/contracts/shared/y",
                    intent=_intent(),
                ),
            ],
        )


def test_contracts_file_rejects_duplicate_owns_collection_names():
    with pytest.raises(ValidationError, match="duplicate collection"):
        ContractsFile(
            module="m",
            owns=[
                OwnedCollection(collection="things", db="main-db"),
                OwnedCollection(collection="things", db="aux-db"),
            ],
        )


def test_contracts_file_rejects_duplicate_external_dependency_ids():
    with pytest.raises(ValidationError, match="duplicate id"):
        ContractsFile(
            module="m",
            external_dependencies=[
                ExternalDependency(id="ext", kind="external_http"),
                ExternalDependency(id="ext", kind="external_queue"),
            ],
        )


def test_contracts_file_rejects_duplicate_integration_ac_capabilities():
    with pytest.raises(ValidationError, match="duplicate capability"):
        ContractsFile(
            module="m",
            integration_ac=[
                IntegrationAcceptance(capability="cap-x", must=["a"]),
                IntegrationAcceptance(capability="cap-x", must=["b"]),
            ],
        )


# ---- BoundariesFile: module-isolation declaration -----------------------


def test_boundaries_file_minimal_valid():
    bf = BoundariesFile(module="job-posting")
    assert bf.spec_version == 1
    assert bf.module == "job-posting"
    assert bf.ontology == []
    assert bf.internal.allowed_modules == []
    assert bf.internal.forbidden_modules == []
    assert bf.external.allowed == []
    assert bf.external.forbidden == []


def test_boundaries_file_full_round_trip():
    bf = BoundariesFile(
        module="job-posting",
        ontology=[OntologyTerm(term="Candidate", definition="An applicant.")],
        internal=InternalBoundaries(
            allowed_modules=["candidate", "shared-types"],
            forbidden_modules=["billing", "auth"],
            rationale="reads candidate refs only",
        ),
        external=ExternalBoundaries(
            allowed=["httpx", "pydantic"],
            forbidden=["requests"],
            rationale="standardized on httpx",
        ),
        change_log=[ChangeLogEntry(revision=1, date=date(2026, 6, 8), summary="init")],
    )
    assert bf.internal.forbidden_modules == ["billing", "auth"]
    assert bf.external.forbidden == ["requests"]
    assert bf.ontology[0].term == "Candidate"


def test_boundaries_file_rejects_non_kebab_module():
    with pytest.raises(ValidationError, match="BoundariesFile.module"):
        BoundariesFile(module="Job Posting")


def test_internal_boundaries_rejects_allow_forbid_overlap():
    with pytest.raises(ValidationError, match="both"):
        InternalBoundaries(
            allowed_modules=["candidate", "billing"],
            forbidden_modules=["billing"],
        )


def test_boundaries_file_forbids_extra_key():
    with pytest.raises(ValidationError, match="extra"):
        BoundariesFile(module="m", bogus="nope")


def test_internal_boundaries_forbids_extra_key():
    with pytest.raises(ValidationError, match="extra"):
        InternalBoundaries(allowed_modules=["a"], bogus="x")


def test_external_boundaries_forbids_extra_key():
    with pytest.raises(ValidationError, match="extra"):
        ExternalBoundaries(forbidden=["requests"], bogus="x")


def test_ontology_term_forbids_extra_key():
    with pytest.raises(ValidationError, match="extra"):
        OntologyTerm(term="t", definition="d", bogus="x")


def test_boundaries_file_model_dump_round_trips():
    """Step 3 writes the canonical model_dump to disk; it must re-parse
    identically (sort_keys=False is irrelevant to value identity)."""
    bf = BoundariesFile(
        module="job-posting",
        internal=InternalBoundaries(forbidden_modules=["billing"]),
        external=ExternalBoundaries(forbidden=["requests"]),
    )
    again = BoundariesFile.model_validate(bf.model_dump())
    assert again == bf
