"""Tests for shared field-shape validators (Track A Final).

Covers ``jig/schemas/_validators.py`` directly and the integration into
po / arch / plan / dev_env / frontend / design_system schemas.  Each rule
gets accept + reject coverage; the per-schema integration tests verify
the validator fires on the right fields.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from jig.intent import Intent
from jig.schemas._validators import (
    validate_kebab_id,
    validate_kebab_id_list,
    validate_project_uri_shape,
    validate_tz_aware,
)


# ---------------------------------------------------------------------------
# Pure validator tests
# ---------------------------------------------------------------------------


def test_kebab_accepts_lowercase_alphanum():
    assert validate_kebab_id("foo-bar-1", "f") == "foo-bar-1"


def test_kebab_accepts_single_segment():
    assert validate_kebab_id("foo", "f") == "foo"


@pytest.mark.parametrize(
    "bad", ["Foo", "foo_bar", "FOO", "foo--bar", "-foo", "foo-", " foo", ""]
)
def test_kebab_rejects_non_kebab(bad: str):
    with pytest.raises(ValueError, match="kebab"):
        validate_kebab_id(bad, "f")


def test_kebab_id_list_validates_each_entry():
    assert validate_kebab_id_list(["a", "b-c"], "f") == ["a", "b-c"]


def test_kebab_id_list_rejects_bad_entry():
    with pytest.raises(ValueError, match="kebab"):
        validate_kebab_id_list(["a", "B"], "f")


def test_uri_shape_accepts_well_formed():
    val = validate_project_uri_shape("project://arch/architecture")
    assert val == "project://arch/architecture"


def test_uri_shape_accepts_with_revision_and_fragment():
    val = validate_project_uri_shape(
        "project://arch/modules/m/contracts@revision:5#owns/products"
    )
    assert "revision:5" in val


@pytest.mark.parametrize(
    "bad",
    [
        "garbage",
        "ticket://x",
        "project://garbage/x",  # unknown authority
        "project://arch/Bad/Path",  # uppercase segment
        "project://arch/x@revision:abc",  # malformed revision
    ],
)
def test_uri_shape_rejects_bad_strings(bad: str):
    with pytest.raises(ValueError):
        validate_project_uri_shape(bad)


def test_tz_aware_accepts_utc_datetime():
    dt = datetime(2026, 5, 1, tzinfo=timezone.utc)
    assert validate_tz_aware(dt, "f") is dt


def test_tz_aware_rejects_naive_datetime():
    naive = datetime(2026, 5, 1)
    with pytest.raises(ValueError, match="timezone"):
        validate_tz_aware(naive, "f")


# ---------------------------------------------------------------------------
# Schema integration — po
# ---------------------------------------------------------------------------


def test_product_non_goal_id_must_be_kebab():
    from jig.schemas.po import ProductNonGoal

    with pytest.raises(ValidationError, match="kebab"):
        ProductNonGoal(id="Bad-Id", text="x")


def test_product_non_goal_kebab_id_accepted():
    from jig.schemas.po import ProductNonGoal

    ng = ProductNonGoal(id="no-multi-user", text="x")
    assert ng.id == "no-multi-user"


def test_suite_id_must_be_kebab():
    from jig.schemas.po import Suite

    with pytest.raises(ValidationError, match="kebab"):
        Suite(id="Suite_A", title="t", summary="s")


def test_suite_capabilities_list_must_be_kebab():
    from jig.schemas.po import Suite

    with pytest.raises(ValidationError, match="kebab"):
        Suite(id="suite-a", title="t", summary="s", capabilities=["Cap"])


def test_project_generated_at_rejects_naive():
    from jig.schemas.po import Project

    with pytest.raises(ValidationError, match="timezone"):
        Project(
            name="x",
            pitch="x",
            problem="x",
            audience="x",
            generated_at=datetime(2026, 5, 1),
        )


def test_discovery_doc_generated_at_rejects_naive():
    from jig.schemas.po import DiscoveryDoc

    with pytest.raises(ValidationError, match="timezone"):
        DiscoveryDoc(
            project_name="x",
            generated_at=datetime(2026, 5, 1),
        )


# ---------------------------------------------------------------------------
# Schema integration — arch
# ---------------------------------------------------------------------------


def _intent() -> Intent:
    return Intent(problem="P", simplest_solution="S")


def test_module_id_must_be_kebab():
    from jig.schemas.arch import Module

    with pytest.raises(ValidationError, match="kebab"):
        Module(
            id="Bad_Module",
            title="t",
            summary="s",
            intent=_intent(),
        )


def test_module_id_kebab_accepted():
    from jig.schemas.arch import Module

    m = Module(
        id="catalog-ingest",
        title="t",
        summary="s",
        intent=_intent(),
    )
    assert m.id == "catalog-ingest"


def test_data_store_id_must_be_kebab():
    from jig.schemas.arch import DataStore

    with pytest.raises(ValidationError, match="kebab"):
        DataStore(id="Main_DB", kind="postgres")


def test_risk_id_must_be_kebab():
    from jig.schemas.arch import Risk, RiskImpact, RiskLikelihood, RiskStatus

    with pytest.raises(ValidationError, match="kebab"):
        Risk(
            id="R_001",
            text="x",
            impact=RiskImpact.LOW,
            likelihood=RiskLikelihood.LOW,
            status=RiskStatus.OPEN,
        )


def test_shared_contract_payload_ref_validates_uri_shape():
    from jig.schemas.arch import SharedContract

    with pytest.raises(ValidationError, match="prefix"):
        SharedContract(
            id="sc",
            type="event",
            payload_ref="not-a-uri",
        )


def test_shared_contract_schema_ref_validates_uri_shape():
    from jig.schemas.arch import SharedContract

    with pytest.raises(ValidationError, match="prefix"):
        SharedContract(
            id="sc",
            type="data",
            schema_ref="garbage",
        )


def test_shared_contract_well_formed_uri_accepted():
    from jig.schemas.arch import SharedContract

    sc = SharedContract(
        id="sc",
        type="event",
        payload_ref="project://arch/contracts/shared/x",
    )
    assert sc.payload_ref == "project://arch/contracts/shared/x"


def test_data_contract_schema_ref_validates_uri_shape():
    from jig.schemas.arch import DataContract

    with pytest.raises(ValidationError, match="prefix"):
        DataContract(id="dc", schema_ref="not-a-uri", intent=_intent())


def test_owned_collection_schema_ref_validates_uri_shape():
    from jig.schemas.arch import OwnedCollection

    with pytest.raises(ValidationError, match="prefix"):
        OwnedCollection(collection="x", db="y", schema_ref="garbage")


def test_risk_dependent_contracts_validates_uri_shape():
    from jig.schemas.arch import Risk, RiskImpact, RiskLikelihood, RiskStatus

    with pytest.raises(ValidationError, match="prefix"):
        Risk(
            id="r-x",
            text="x",
            impact=RiskImpact.LOW,
            likelihood=RiskLikelihood.LOW,
            status=RiskStatus.SPIKE_PROPOSED,
            dependent_contracts=["nope"],
            intent=_intent(),
        )


def test_change_log_entry_summary_required():
    from jig.schemas.arch import ChangeLogEntry

    cl = ChangeLogEntry(revision=1, date=date(2026, 5, 1), summary="x")
    assert cl.revision == 1


def test_architecture_generated_at_rejects_naive():
    from jig.schemas.arch import Architecture

    with pytest.raises(ValidationError, match="timezone"):
        Architecture(generated_at=datetime(2026, 5, 1))


def test_cascade_proposal_uri_validates_uri_shape():
    from jig.schemas.arch import CascadeContractDisposition

    with pytest.raises(ValidationError, match="prefix"):
        CascadeContractDisposition(uri="garbage")


# ---------------------------------------------------------------------------
# Schema integration — plan
# ---------------------------------------------------------------------------


def test_epic_id_must_be_kebab():
    from jig.schemas.plan import Epic

    with pytest.raises(ValidationError, match="kebab"):
        Epic(id="Epic_1", title="t", suite="s", intent=_intent())


def test_epic_id_kebab_accepted():
    from jig.schemas.plan import Epic

    e = Epic(id="catalog-ingest", title="t", suite="s", intent=_intent())
    assert e.id == "catalog-ingest"


def test_build_plan_generated_at_rejects_naive():
    from jig.schemas.plan import BuildPlan

    with pytest.raises(ValidationError, match="timezone"):
        BuildPlan(project="x", generated_at=datetime(2026, 5, 1))


def test_stalled_ticket_blocked_since_rejects_naive():
    from jig.schemas.plan import StalledTicket

    with pytest.raises(ValidationError, match="timezone"):
        StalledTicket(
            ticket="t-x", reason="r", blocked_since=datetime(2026, 5, 1)
        )


# ---------------------------------------------------------------------------
# Schema integration — dev_env
# ---------------------------------------------------------------------------


def test_manifest_service_id_must_be_kebab():
    from jig.schemas.dev_env import ManifestService

    with pytest.raises(ValidationError, match="kebab"):
        ManifestService(
            id="Bad_Service",
            kind="postgres",
            strategy="shared_namespaced",
            namespace_template="x",
        )


def test_manifest_service_id_kebab_accepted():
    from jig.schemas.dev_env import ManifestService

    s = ManifestService(
        id="main-db",
        kind="postgres",
        strategy="shared_namespaced",
        namespace_template="agent_{ticket_id}",
    )
    assert s.id == "main-db"


def test_dev_manifest_generated_at_rejects_naive():
    from jig.schemas.dev_env import DevManifest

    with pytest.raises(ValidationError, match="timezone"):
        DevManifest(generated_at=datetime(2026, 5, 1))


# ---------------------------------------------------------------------------
# Schema integration — frontend
# ---------------------------------------------------------------------------


def test_frontend_spec_generated_at_rejects_naive():
    from jig.schemas.frontend import FrontendSpec

    with pytest.raises(ValidationError, match="timezone"):
        FrontendSpec(intent=_intent(), generated_at=datetime(2026, 5, 1))


# ---------------------------------------------------------------------------
# Schema integration — design_system
# ---------------------------------------------------------------------------


def test_design_token_id_must_be_kebab():
    from jig.schemas.design_system import DesignToken

    with pytest.raises(ValidationError, match="kebab"):
        DesignToken(id="Color_Bg", kind="color", value="#fff")


def test_component_id_must_be_kebab():
    from jig.schemas.design_system import Component

    with pytest.raises(ValidationError, match="kebab"):
        Component(id="Button_X", name="B")


def test_component_variant_id_must_be_kebab():
    from jig.schemas.design_system import ComponentVariant

    with pytest.raises(ValidationError, match="kebab"):
        ComponentVariant(id="Primary_X")
