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
