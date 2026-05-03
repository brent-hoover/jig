"""Tests for the v2 build-plan extension fields on ``jig.ticket.Ticket``.

The fields are optional during the v2 build so v1 records still load. The
Planner PM populates them when the build plan owns the ticket.
"""
from __future__ import annotations

from jig.ticket import Ticket, WorkType


def _base_kwargs() -> dict:
    return {
        "id": "t-001",
        "work_type": WorkType.FEATURE,
        "title": "x",
        "created_by": "po",
    }


def test_ticket_loads_without_v2_fields():
    """v1 records must still construct cleanly."""
    t = Ticket(**_base_kwargs())
    assert t.suite_id is None
    assert t.module_id is None
    assert t.capability_ids == []
    assert t.epic_id is None
    assert t.layer is None
    assert t.dev_tier is None
    assert t.reviewer_set == []
    assert t.context_hints == {}
    assert t.risks_addressed == []
    assert t.done_when is None


def test_ticket_with_full_v2_extensions():
    t = Ticket(
        **_base_kwargs(),
        suite_id="catalog",
        module_id="catalog-ingest",
        capability_ids=["shopify-connect", "normalize-skus"],
        epic_id="catalog-ingest",
        layer="bones",
        dev_tier="senior",
        reviewer_set=["contract-compliance", "spec-compliance"],
        context_hints={
            "always_inject": ["cross-cutting-policies"],
            "auto_inject_uris": [
                "project://arch/modules/catalog-ingest/contracts",
            ],
            "pull_available": True,
        },
        risks_addressed=["r-shopify-delta"],
        done_when="Data flows: customer source → ingest → products → event published.",
    )
    assert t.suite_id == "catalog"
    assert t.module_id == "catalog-ingest"
    assert t.layer == "bones"
    assert t.dev_tier == "senior"
    assert "contract-compliance" in t.reviewer_set
    assert t.context_hints["pull_available"] is True
    assert t.done_when.startswith("Data flows")


def test_ticket_v2_fields_round_trip_via_dict():
    t = Ticket(
        **_base_kwargs(),
        suite_id="catalog",
        layer="bones",
        capability_ids=["c1"],
    )
    blob = t.model_dump()
    t2 = Ticket.model_validate(blob)
    assert t2 == t
