"""Tests for v2 SA schemas — Architecture, Module, ContractsFile, Risk."""
from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from jig.intent import ComplicationsConsidered, Intent
from jig.schemas.arch import (
    Architecture,
    BehavioralContract,
    ChangeLogEntry,
    ContractsFile,
    DataContract,
    DataStore,
    DevProvisioning,
    ExternalDependency,
    IntegrationAcceptance,
    Module,
    OwnedCollection,
    Risk,
    RiskImpact,
    RiskLikelihood,
    RiskStatus,
    SharedContract,
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
