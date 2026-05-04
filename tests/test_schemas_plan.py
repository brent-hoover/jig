"""Tests for v2 PM schemas — BuildPlan, Epic, LayerStatus."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from jig.intent import Intent
from jig.schemas.plan import (
    BuildPlan,
    Epic,
    EpicLayers,
    LayerStatus,
    LayerStatusEnum,
    OrderingRule,
    StalledTicket,
)


def _intent() -> Intent:
    return Intent(problem="P", simplest_solution="S")


def test_build_plan_minimum_valid():
    bp = BuildPlan(project="jig-search")
    assert bp.spec_version == 1
    assert bp.revision == 1
    assert bp.ordering_rule == OrderingRule.BONES_FIRST
    assert bp.epics == []


def test_epic_requires_intent():
    with pytest.raises(ValidationError, match="intent"):
        Epic(id="e", title="t", suite="s")


def test_epic_with_full_shape():
    e = Epic(
        id="catalog-ingest",
        title="Catalog ingest",
        suite="catalog",
        modules=["catalog-ingest"],
        layers=EpicLayers(
            bones=LayerStatus(
                status=LayerStatusEnum.IN_PROGRESS,
                tickets=["tb-catalog-ingest"],
            ),
            mvp=LayerStatus(tickets=["t-shopify-oauth", "t-csv-parser"]),
        ),
        risks_addressed=["r-shopify-delta"],
        intent=_intent(),
    )
    assert e.layers.bones.status == LayerStatusEnum.IN_PROGRESS
    assert e.layers.mvp.status == LayerStatusEnum.NOT_STARTED  # default
    assert e.layers.final.tickets == []  # default


def test_layer_status_defaults():
    ls = LayerStatus()
    assert ls.status == LayerStatusEnum.NOT_STARTED
    assert ls.tickets == []


def test_build_plan_with_stalled_and_open_questions():
    from jig.schemas.arch import OpenQuestion

    bp = BuildPlan(
        project="jig-search",
        epics=[Epic(
            id="catalog",
            title="Catalog",
            suite="catalog",
            intent=_intent(),
        )],
        stalled=[StalledTicket(
            ticket="t-shopify-oauth",
            reason="blocked-on-spike",
            blocked_since=datetime(2026, 5, 1, 12, tzinfo=timezone.utc),
            spike="spike-shopify-delta",
        )],
        open_questions=[OpenQuestion(
            id="q-feature-priority",
            text="MVP order: catalog before categorization?",
        )],
    )
    assert bp.stalled[0].spike == "spike-shopify-delta"
    assert bp.open_questions[0].id == "q-feature-priority"


def test_build_plan_rejects_extra():
    with pytest.raises(ValidationError, match="extra"):
        BuildPlan(project="x", mystery="y")


def test_build_plan_round_trips():
    bp = BuildPlan(
        project="jig-search",
        epics=[Epic(id="e", title="t", suite="s",
                    layers=EpicLayers(bones=LayerStatus(tickets=["tb-1"])),
                    intent=_intent())],
    )
    blob = bp.model_dump(mode="json")
    bp2 = BuildPlan.model_validate(blob)
    assert bp2 == bp


# ---------------------------------------------------------------------------
# Block 4 — non-tautological rejection + cross-field tests (issue #7)
# ---------------------------------------------------------------------------
#
# Existing tests above include round-trip-only checks (build_plan_round_trips,
# build_plan_with_stalled_and_open_questions). The reviewer asked for
# rejection coverage and cross-field semantics. Existing tests stay — these
# add the missing proofs.


# ---- Epic / EpicLayers / LayerStatus: rejection ---------------------------


def test_epic_rejects_non_kebab_suite_id():
    """Suite id propagates to PO's suites.yaml lookup — kebab is required."""
    with pytest.raises(ValidationError, match="kebab"):
        Epic(id="e", title="t", suite="Catalog_Suite", intent=_intent())


def test_epic_rejects_missing_intent():
    """Smoke check: intent is the v2-mandatory carve-up rationale."""
    with pytest.raises(ValidationError, match="intent"):
        Epic(id="e", title="t", suite="s")  # type: ignore[call-arg]


def test_layer_status_rejects_unknown_status_enum():
    with pytest.raises(ValidationError):
        LayerStatus(status="halfway-done")  # type: ignore[arg-type]


def test_pydantic_extra_forbid_rejects_unknown_keys_on_layer_status():
    with pytest.raises(ValidationError, match="extra"):
        LayerStatus(status=LayerStatusEnum.NOT_STARTED, owner="x")  # type: ignore[call-arg]


def test_pydantic_extra_forbid_rejects_unknown_keys_on_epic():
    with pytest.raises(ValidationError, match="extra"):
        Epic(  # type: ignore[call-arg]
            id="e",
            title="t",
            suite="s",
            intent=_intent(),
            priority="p1",
        )


# ---- BuildPlan: rejection coverage ----------------------------------------


def test_build_plan_rejects_naive_generated_at():
    with pytest.raises(ValidationError, match="timezone"):
        BuildPlan(project="x", generated_at=datetime(2026, 5, 1))


def test_build_plan_rejects_naive_last_revised():
    """Validator covers both timestamp fields under one rule — pin both."""
    with pytest.raises(ValidationError, match="timezone"):
        BuildPlan(project="x", last_revised=datetime(2026, 5, 1))


def test_build_plan_rejects_revision_below_one():
    """``revision`` must be >= 1; revision 0 implies "no plan written yet"
    which the build-plan store treats as absent — so authoring a plan with
    revision=0 is a contract bug."""
    with pytest.raises(ValidationError, match="revision"):
        BuildPlan(project="x", revision=0)


def test_build_plan_rejects_unknown_ordering_rule():
    with pytest.raises(ValidationError):
        BuildPlan(project="x", ordering_rule="random")  # type: ignore[arg-type]


def test_stalled_ticket_rejects_missing_blocked_since():
    with pytest.raises(ValidationError, match="blocked_since"):
        StalledTicket(ticket="t-x", reason="r")  # type: ignore[call-arg]


# ---- BuildPlan cross-field semantics --------------------------------------
#
# The reviewer asked for cross-field semantic tests "where applicable".
# The plan schema doesn't currently enforce non-overlapping ticket ids
# across epics — that lives in the Planner's plan_finalize path. As with
# Architecture's module-id uniqueness, we document the chain here so the
# claim is visible at the schema-test layer.


def test_build_plan_currently_accepts_overlapping_ticket_ids_across_epics():
    """Schema-layer claim: ticket-id uniqueness lives in the Planner, not
    in the Pydantic model. If schema-side enforcement lands later, flip
    this assertion to ``pytest.raises``."""
    bp = BuildPlan(
        project="x",
        epics=[
            Epic(
                id="e1",
                title="t",
                suite="s",
                layers=EpicLayers(bones=LayerStatus(tickets=["tb-shared"])),
                intent=_intent(),
            ),
            Epic(
                id="e2",
                title="t",
                suite="s",
                layers=EpicLayers(bones=LayerStatus(tickets=["tb-shared"])),
                intent=_intent(),
            ),
        ],
    )
    bones_tickets = [
        t for e in bp.epics for t in e.layers.bones.tickets
    ]
    assert bones_tickets == ["tb-shared", "tb-shared"]


def test_build_plan_revision_increments_round_trip():
    """Companion happy path: revision ≥ 1 round-trips intact across dump/validate."""
    bp = BuildPlan(project="x", revision=7)
    blob = bp.model_dump(mode="json")
    bp2 = BuildPlan.model_validate(blob)
    assert bp2.revision == 7
