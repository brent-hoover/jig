"""Tests for programmatic URI constructors (Track A Final).

One test per constructor: confirm the rendered string + that the input
goes through the kebab-case validator.  The constructors are tiny so the
tests are mechanical — coverage value is in catching typos that would
otherwise route real artifacts to the wrong path.
"""

from __future__ import annotations

import pytest

from jig.uri import parse_project_uri
from jig.uri.constructors import (
    arch_architecture_uri,
    arch_behavioral_contract_uri,
    arch_contracts_uri,
    arch_data_contract_uri,
    arch_integration_ac_uri,
    arch_module_uri,
    arch_risk_uri,
    arch_shared_contract_uri,
    design_frontend_uri,
    design_system_brand_uri,
    design_system_components_uri,
    design_system_tokens_uri,
    design_wireframe_uri,
    plan_build_uri,
    plan_deferred_uri,
    plan_epic_uri,
    plan_layer_uri,
    plan_ticket_uri,
    spec_ac_uri,
    spec_behavior_uri,
    spec_capability_uri,
    spec_discovery_uri,
    spec_journey_uri,
    spec_ontology_term_uri,
    spec_ontology_uri,
    spec_persona_uri,
    spec_playback_uri,
    spec_project_structured_uri,
    spec_project_uri,
    spec_suite_brief_uri,
    spec_suite_structured_uri,
    spec_suite_uri,
    spec_suites_uri,
    store_event_uri,
    store_thread_entry_uri,
    store_thread_uri,
    store_ticket_uri,
)

# ---------------------------------------------------------------------------
# spec authority
# ---------------------------------------------------------------------------


def test_spec_project_uri():
    assert spec_project_uri() == "project://spec/project"


def test_spec_discovery_uri():
    assert spec_discovery_uri() == "project://spec/discovery"


def test_spec_persona_uri():
    assert spec_persona_uri("merchant") == "project://spec/discovery/personas/merchant"


def test_spec_persona_uri_rejects_non_kebab():
    with pytest.raises(ValueError, match="kebab"):
        spec_persona_uri("Bad_Id")


def test_spec_journey_uri():
    assert (
        spec_journey_uri("j-merchant-onboarding")
        == "project://spec/discovery/journeys/j-merchant-onboarding"
    )


def test_spec_playback_uri():
    assert spec_playback_uri("j-x") == "project://spec/discovery/playbacks/j-x"


def test_spec_ontology_uri():
    assert spec_ontology_uri() == "project://spec/ontology"


def test_spec_ontology_term_uri():
    assert (
        spec_ontology_term_uri("merchant") == "project://spec/ontology/terms/merchant"
    )


def test_spec_suites_uri():
    assert spec_suites_uri() == "project://spec/suites"


def test_spec_suite_uri():
    assert spec_suite_uri("catalog") == "project://spec/suites/catalog"


def test_spec_suite_brief_uri():
    assert spec_suite_brief_uri("catalog") == "project://spec/suites/catalog/brief"


def test_spec_suite_structured_uri():
    assert spec_suite_structured_uri("catalog") == "project://spec/suites/catalog/spec"


def test_spec_capability_uri():
    assert (
        spec_capability_uri("catalog", "normalize-skus")
        == "project://spec/suites/catalog/capabilities/normalize-skus"
    )


def test_spec_behavior_uri():
    assert (
        spec_behavior_uri("catalog", "normalize-skus", "trim-whitespace")
        == "project://spec/suites/catalog/capabilities/normalize-skus"
        "/behaviors/trim-whitespace"
    )


def test_spec_ac_uri():
    assert (
        spec_ac_uri("catalog", "norm", "trim", "ac-1")
        == "project://spec/suites/catalog/capabilities/norm"
        "/behaviors/trim/ac/ac-1"
    )


def test_spec_project_structured_uri():
    assert spec_project_structured_uri() == "project://spec/project_structured"


# ---------------------------------------------------------------------------
# arch authority
# ---------------------------------------------------------------------------


def test_arch_architecture_uri_no_revision():
    assert arch_architecture_uri() == "project://arch/architecture"


def test_arch_architecture_uri_with_revision():
    assert arch_architecture_uri(revision=5) == "project://arch/architecture@revision:5"


def test_arch_architecture_uri_rejects_non_positive_revision():
    with pytest.raises(ValueError, match="revision"):
        arch_architecture_uri(revision=0)


def test_arch_module_uri():
    assert arch_module_uri("catalog-ingest") == "project://arch/modules/catalog-ingest"


def test_arch_module_uri_rejects_non_kebab():
    with pytest.raises(ValueError, match="kebab"):
        arch_module_uri("Bad_Module")


def test_arch_contracts_uri_basic():
    assert (
        arch_contracts_uri("catalog-ingest")
        == "project://arch/modules/catalog-ingest/contracts"
    )


def test_arch_contracts_uri_with_fragment_and_revision():
    assert (
        arch_contracts_uri("catalog-ingest", fragment="owns/products", revision=7)
        == "project://arch/modules/catalog-ingest/contracts"
        "@revision:7#owns/products"
    )


def test_arch_shared_contract_uri():
    assert (
        arch_shared_contract_uri("product") == "project://arch/contracts/shared/product"
    )


def test_arch_integration_ac_uri_capability_only():
    assert (
        arch_integration_ac_uri("catalog-ingest", "shopify-connect")
        == "project://arch/modules/catalog-ingest/contracts"
        "#integration_ac/shopify-connect"
    )


def test_arch_integration_ac_uri_with_must_index():
    assert (
        arch_integration_ac_uri("catalog-ingest", "shopify-connect", must_index=2)
        == "project://arch/modules/catalog-ingest/contracts"
        "#integration_ac/shopify-connect/must/2"
    )


def test_arch_integration_ac_uri_rejects_negative_index():
    with pytest.raises(ValueError, match="must_index"):
        arch_integration_ac_uri("m", "c", must_index=-1)


def test_arch_behavioral_contract_uri():
    assert (
        arch_behavioral_contract_uri("catalog-ingest", "ingest-atomic")
        == "project://arch/modules/catalog-ingest/contracts"
        "#behavioral_contracts/ingest-atomic"
    )


def test_arch_data_contract_uri():
    assert (
        arch_data_contract_uri("catalog-ingest", "product-shape")
        == "project://arch/modules/catalog-ingest/contracts"
        "#data_contracts/product-shape"
    )


def test_arch_risk_uri():
    assert arch_risk_uri("r-shopify-delta") == "project://arch/risks/r-shopify-delta"


# ---------------------------------------------------------------------------
# design authority
# ---------------------------------------------------------------------------


def test_design_wireframe_uri():
    assert (
        design_wireframe_uri("post-a-job") == "project://design/wireframes/post-a-job"
    )


def test_design_system_tokens_uri():
    assert design_system_tokens_uri() == "project://design/system/tokens"


def test_design_system_components_uri():
    assert design_system_components_uri() == "project://design/system/components"


def test_design_system_brand_uri():
    assert design_system_brand_uri() == "project://design/system/brand"


def test_design_frontend_uri():
    assert design_frontend_uri() == "project://design/frontend"


# ---------------------------------------------------------------------------
# plan authority
# ---------------------------------------------------------------------------


def test_plan_build_uri():
    assert plan_build_uri() == "project://plan/build"


def test_plan_epic_uri():
    assert (
        plan_epic_uri("catalog-ingest") == "project://plan/build/epics/catalog-ingest"
    )


def test_plan_layer_uri_bones():
    assert (
        plan_layer_uri("catalog", "bones")
        == "project://plan/build/epics/catalog/layers/bones"
    )


def test_plan_layer_uri_mvp():
    assert (
        plan_layer_uri("catalog", "mvp")
        == "project://plan/build/epics/catalog/layers/mvp"
    )


def test_plan_layer_uri_rejects_unknown_layer():
    with pytest.raises(ValueError, match="layer"):
        plan_layer_uri("e", "extra")  # type: ignore[arg-type]


def test_plan_ticket_uri():
    assert plan_ticket_uri("t-001") == "project://plan/tickets/t-001"


def test_plan_deferred_uri():
    assert plan_deferred_uri("d-001") == "project://plan/deferred/d-001"


# ---------------------------------------------------------------------------
# store authority
# ---------------------------------------------------------------------------


def test_store_ticket_uri():
    assert store_ticket_uri("t-001") == "project://store/tickets/t-001"


def test_store_thread_uri():
    assert store_thread_uri("t-001") == "project://store/threads/t-001"


def test_store_thread_entry_uri():
    assert (
        store_thread_entry_uri("t-001", "e-42")
        == "project://store/threads/t-001/entries/e-42"
    )


def test_store_event_uri():
    assert store_event_uri("ev-1") == "project://store/events/ev-1"


# ---------------------------------------------------------------------------
# Round-trip — every constructor produces a parseable URI
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "uri",
    [
        spec_project_uri(),
        spec_discovery_uri(),
        spec_persona_uri("merchant"),
        spec_journey_uri("j-x"),
        spec_playback_uri("j-x"),
        spec_ontology_uri(),
        spec_ontology_term_uri("term-a"),
        spec_suites_uri(),
        spec_suite_uri("catalog"),
        spec_suite_brief_uri("catalog"),
        spec_suite_structured_uri("catalog"),
        spec_capability_uri("catalog", "norm"),
        spec_behavior_uri("catalog", "norm", "trim"),
        spec_ac_uri("catalog", "norm", "trim", "ac-1"),
        spec_project_structured_uri(),
        arch_architecture_uri(),
        arch_architecture_uri(revision=3),
        arch_module_uri("m-x"),
        arch_contracts_uri("m-x"),
        arch_contracts_uri("m-x", fragment="owns/products", revision=4),
        arch_shared_contract_uri("product"),
        arch_integration_ac_uri("m-x", "cap"),
        arch_integration_ac_uri("m-x", "cap", must_index=1),
        arch_behavioral_contract_uri("m-x", "bc-1"),
        arch_data_contract_uri("m-x", "dc-1"),
        arch_risk_uri("r-x"),
        design_wireframe_uri("post-a-job"),
        design_system_tokens_uri(),
        design_system_components_uri(),
        design_system_brand_uri(),
        design_frontend_uri(),
        plan_build_uri(),
        plan_epic_uri("e-x"),
        plan_layer_uri("e-x", "bones"),
        plan_ticket_uri("t-1"),
        plan_deferred_uri("d-1"),
        store_ticket_uri("t-1"),
        store_thread_uri("t-1"),
        store_thread_entry_uri("t-1", "e-1"),
        store_event_uri("ev-1"),
    ],
)
def test_constructor_outputs_parse_cleanly(uri: str):
    parsed = parse_project_uri(uri)
    assert parsed.authority in ("spec", "arch", "design", "plan", "store")
