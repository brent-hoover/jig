"""Tests for v2 PO schemas — L0 Project + ProductNonGoal."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from jig.schemas.po import (
    CapabilityRosterEntry,
    DiscoveryDoc,
    Journey,
    Persona,
    Project,
    ProductNonGoal,
    Suite,
    SuitesIndex,
)


def test_project_minimum_valid():
    p = Project(
        name="jig-search",
        pitch="hosted search SaaS for ecommerce",
        problem="merchants run blind without good search",
        audience="ecommerce ops staff at SMBs",
    )
    assert p.spec_version == 2
    assert p.non_goals == []
    assert p.generated_at is not None


def test_project_with_non_goals():
    p = Project(
        name="jig-search",
        pitch="x",
        problem="y",
        audience="z",
        non_goals=[
            ProductNonGoal(id="no-cms", text="We will not build a CMS",
                           rationale="out of scope for v2"),
            ProductNonGoal(id="no-recs", text="No recommendation engine"),
        ],
    )
    assert len(p.non_goals) == 2
    assert p.non_goals[0].id == "no-cms"
    assert p.non_goals[1].rationale is None


def test_project_rejects_extra_fields():
    with pytest.raises(ValidationError, match="extra"):
        Project(
            name="x",
            pitch="x",
            problem="x",
            audience="x",
            mystery_field="oops",
        )


def test_non_goal_rejects_empty_id():
    with pytest.raises(ValidationError):
        ProductNonGoal(id="", text="x")


def test_non_goal_rejects_empty_text():
    with pytest.raises(ValidationError):
        ProductNonGoal(id="ng-1", text="")


# ---------------------------------------------------------------------------
# Block 4 — non-tautological rejection + cross-field tests (issue #7)
# ---------------------------------------------------------------------------
#
# The reviewer flagged that round-trip (model_dump → model_validate → ==)
# tests prove serialization works but say nothing about whether bad
# operator-authored YAML actually gets rejected. The tests below pin
# rejection behavior and (where the schema claims cross-field semantics)
# cross-field behavior. Existing roundtrip tests stay — they remain
# useful smoke checks.


# ---- Project: missing-required + bad-types --------------------------------


@pytest.mark.parametrize(
    "missing",
    ["name", "pitch", "problem", "audience"],
)
def test_project_rejects_missing_required(missing: str):
    """All four narrative fields are required by the L0 brief schema."""
    kwargs = {"name": "x", "pitch": "x", "problem": "x", "audience": "x"}
    kwargs.pop(missing)
    with pytest.raises(ValidationError, match=missing):
        Project(**kwargs)


def test_project_rejects_naive_generated_at():
    """v2 timestamps are tz-aware; naive datetimes must surface as errors."""
    with pytest.raises(ValidationError, match="timezone"):
        Project(
            name="x",
            pitch="x",
            problem="x",
            audience="x",
            generated_at=datetime(2026, 5, 1),
        )


def test_project_accepts_tz_aware_generated_at_round_trip():
    """Companion to the rejection test: the happy path still loads."""
    p = Project(
        name="x",
        pitch="x",
        problem="x",
        audience="x",
        generated_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
    )
    blob = p.model_dump(mode="json")
    p2 = Project.model_validate(blob)
    assert p2 == p


# ---- Suite + SuitesIndex --------------------------------------------------


def test_suite_rejects_non_kebab_capability():
    """Capability ids in the suite allowlist must be kebab-case."""
    with pytest.raises(ValidationError, match="kebab"):
        Suite(id="catalog", title="t", summary="s", capabilities=["Bad_Cap"])


def test_suite_rejects_extra_field():
    """``extra='forbid'`` chain — operator-typo'd keys must be caught."""
    with pytest.raises(ValidationError, match="extra"):
        Suite(
            id="catalog",
            title="t",
            summary="s",
            owner="x",  # unknown field
        )


def test_suites_index_lookup_by_id_returns_none_for_unknown():
    """Cross-field semantics: ``suite_by_id`` returns ``None`` for misses
    and the matching suite otherwise. Pin the contract so silent renames
    don't break PM lookups."""
    idx = SuitesIndex(
        suites=[
            Suite(id="catalog", title="t", summary="s"),
            Suite(id="search", title="t", summary="s"),
        ]
    )
    assert idx.suite_by_id("catalog").id == "catalog"
    assert idx.suite_by_id("missing") is None


# ---- Journey / DiscoveryDoc -----------------------------------------------


def test_journey_rejects_non_kebab_persona_id():
    with pytest.raises(ValidationError, match="kebab"):
        Journey(
            id="j-x",
            persona_id="Merchant",  # uppercase
            title="t",
            narrative="n",
        )


def test_journey_rejects_naive_persona_kebab_capability():
    """Each entry in ``capability_ids`` must be kebab — bad entries break URIs."""
    with pytest.raises(ValidationError, match="kebab"):
        Journey(
            id="j-x",
            persona_id="merchant",
            title="t",
            narrative="n",
            capability_ids=["good-id", "BAD"],
        )


def test_discovery_doc_round_trip_with_all_layers():
    """Full happy-path round-trip — kept as a smoke check."""
    doc = DiscoveryDoc(
        project_name="jig-search",
        personas=[Persona(id="merchant", description="x")],
        journeys=[
            Journey(
                id="j-onboard",
                persona_id="merchant",
                title="t",
                narrative="n",
                capability_ids=["pick-shop"],
            )
        ],
        capability_roster=[
            CapabilityRosterEntry(
                id="pick-shop", description="x", journey_ids=["j-onboard"]
            )
        ],
    )
    blob = doc.model_dump(mode="json")
    doc2 = DiscoveryDoc.model_validate(blob)
    assert doc2 == doc


# ---- pydantic ``extra='forbid'`` chain (no manual rejection needed) -------
#
# These tests are documented explicitly per the deliverable: where Pydantic
# enforces the rejection for free via ``ConfigDict(extra='forbid')``, name
# the test after the chain so a reader can see WHY there isn't a hand-
# rolled validator.


def test_pydantic_extra_forbid_rejects_unknown_keys_on_persona():
    """Persona uses ``extra='forbid'``. Unknown keys → ValidationError."""
    with pytest.raises(ValidationError, match="extra"):
        Persona(id="x", description="d", role="ceo")  # type: ignore[call-arg]


def test_pydantic_extra_forbid_rejects_unknown_keys_on_capability_roster_entry():
    with pytest.raises(ValidationError, match="extra"):
        CapabilityRosterEntry(  # type: ignore[call-arg]
            id="x", description="d", oops="y"
        )
