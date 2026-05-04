"""Tests for the v2 build-plan extension fields on ``jig.ticket.Ticket``.

The fields are optional during the v2 build so v1 records still load. The
Planner PM populates them when the build plan owns the ticket.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

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


# ---------------------------------------------------------------------------
# Block 4 — schema discipline on the v2 Ticket fields
# ---------------------------------------------------------------------------
#
# These tests pin the kebab-id / tz-aware / literal validators added per
# the v2 code review's important findings #5 + #6. Each validator gets a
# rejection case (the value the validator was added to catch) plus a
# happy-path case (a representative valid value the surrounding code
# already passes).


@pytest.mark.parametrize(
    "field,bad_value",
    [
        ("suite_id", "Catalog"),
        ("module_id", "catalog_ingest"),  # underscore not allowed
        ("epic_id", "Epic-1"),  # uppercase
    ],
)
def test_ticket_optional_id_fields_reject_non_kebab(field: str, bad_value: str):
    """v2 id fields propagate to URIs / paths — non-kebab is a wire-format bug."""
    with pytest.raises(ValidationError, match="kebab"):
        Ticket(**_base_kwargs(), **{field: bad_value})


@pytest.mark.parametrize(
    "field,good_value",
    [
        ("suite_id", "catalog"),
        ("module_id", "catalog-ingest"),
        ("epic_id", "epic-1"),
    ],
)
def test_ticket_optional_id_fields_accept_kebab(field: str, good_value: str):
    t = Ticket(**_base_kwargs(), **{field: good_value})
    assert getattr(t, field) == good_value


def test_ticket_optional_id_fields_accept_none():
    """None must remain valid — these are optional by design."""
    t = Ticket(**_base_kwargs(), suite_id=None, module_id=None, epic_id=None)
    assert t.suite_id is None and t.module_id is None and t.epic_id is None


@pytest.mark.parametrize(
    "field",
    ["capability_ids", "visual_references"],
)
def test_ticket_id_lists_reject_bad_entry(field: str):
    """A single bad entry kills the whole list — fail closed."""
    with pytest.raises(ValidationError, match="kebab"):
        Ticket(**_base_kwargs(), **{field: ["good-id", "Bad_Id"]})


def test_ticket_capability_ids_accept_kebab_list():
    t = Ticket(
        **_base_kwargs(), capability_ids=["shopify-connect", "normalize-skus"]
    )
    assert t.capability_ids == ["shopify-connect", "normalize-skus"]


def test_ticket_visual_references_accept_kebab_list():
    """visual_references become ``.jig/spec/wireframes/<id>.html`` paths."""
    t = Ticket(
        **_base_kwargs(),
        visual_references=["dashboard-overview", "settings-page"],
    )
    assert t.visual_references == ["dashboard-overview", "settings-page"]


@pytest.mark.parametrize(
    "field,bad_value",
    [
        ("layer", "alpha"),
        ("layer", "Bones"),
        ("layer", ""),  # empty must fail too — Literal-like rejection
        ("dev_tier", "junior"),
        ("dev_tier", "Senior"),
        ("dev_tier", ""),
    ],
)
def test_ticket_literal_fields_reject_unknown(field: str, bad_value: str):
    with pytest.raises(ValidationError, match="must be one of"):
        Ticket(**_base_kwargs(), **{field: bad_value})


@pytest.mark.parametrize("v", ["bones", "mvp", "final"])
def test_ticket_layer_accepts_known_value(v: str):
    t = Ticket(**_base_kwargs(), layer=v)
    assert t.layer == v


@pytest.mark.parametrize("v", ["standard", "senior", "sa"])
def test_ticket_dev_tier_accepts_known_value(v: str):
    t = Ticket(**_base_kwargs(), dev_tier=v)
    assert t.dev_tier == v


# ---- timezone-aware datetimes ---------------------------------------------


def test_ticket_created_at_rejects_naive_datetime():
    with pytest.raises(ValidationError, match="timezone"):
        Ticket(**_base_kwargs(), created_at=datetime(2026, 5, 1))


def test_ticket_updated_at_rejects_naive_datetime():
    with pytest.raises(ValidationError, match="timezone"):
        Ticket(**_base_kwargs(), updated_at=datetime(2026, 5, 1))


def test_ticket_deferred_at_rejects_naive_datetime():
    with pytest.raises(ValidationError, match="timezone"):
        Ticket(**_base_kwargs(), deferred_at=datetime(2026, 5, 1))


def test_ticket_deferred_at_accepts_none():
    """The default-state for non-deferred tickets must keep parsing."""
    t = Ticket(**_base_kwargs(), deferred_at=None)
    assert t.deferred_at is None


def test_ticket_timestamps_accept_utc():
    when = datetime(2026, 5, 1, tzinfo=timezone.utc)
    t = Ticket(
        **_base_kwargs(),
        created_at=when,
        updated_at=when,
        deferred_at=when,
    )
    assert t.created_at == when
    assert t.updated_at == when
    assert t.deferred_at == when
