"""Phase 1 product-definition artifact tests.

Covers:
  - Tradeoff ledger schema + store round-trip
  - TradeoffComplianceReviewer fires on capability-id overlaps
  - DoneEnoughBlock and GivenWhenThen on Capability
  - GivenWhenThen on Ticket
  - Reviewer dispatch: tradeoff-compliance in bones and MVP defaults
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jig.schemas.tradeoffs import Tradeoff, TradeoffLedger
from jig.spec_schema import (
    Capability,
    CapabilityState,
    DoneEnoughBlock,
    GivenWhenThen,
)
from jig.ticket import Ticket, TicketStatus, WorkType


# ---- Tradeoff schema -------------------------------------------------------


def test_tradeoff_minimal():
    t = Tradeoff(
        id="no-caching-at-bones",
        decision_summary="No response caching at the bones layer.",
        deferred=["HTTP response caching", "TTL logic"],
        deferred_to="mvp",
        rationale="Adds complexity before the core flow is proven.",
    )
    assert t.deferred_to == "mvp"
    assert t.capability_ids == []


def test_tradeoff_with_capability_ids():
    t = Tradeoff(
        id="no-auth",
        decision_summary="No authentication in scope.",
        deferred=["JWT validation", "session management"],
        deferred_to="never",
        rationale="Single-user local tool; auth is out of scope permanently.",
        capability_ids=["login", "register"],
    )
    assert "login" in t.capability_ids


def test_tradeoff_rejects_extra_fields():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Tradeoff(
            id="x",
            decision_summary="x",
            deferred=[],
            deferred_to="mvp",
            rationale="x",
            unknown_field="bad",  # type: ignore[call-arg]
        )


def test_tradeoff_ledger_for_capability():
    t1 = Tradeoff(
        id="t1",
        decision_summary="s",
        deferred=["a"],
        deferred_to="mvp",
        rationale="r",
        capability_ids=["cap-a", "cap-b"],
    )
    t2 = Tradeoff(
        id="t2",
        decision_summary="s2",
        deferred=["b"],
        deferred_to="final",
        rationale="r2",
        capability_ids=["cap-b"],
    )
    ledger = TradeoffLedger(tradeoffs=[t1, t2])
    assert ledger.for_capability("cap-a") == [t1]
    assert set(t.id for t in ledger.for_capability("cap-b")) == {"t1", "t2"}
    assert ledger.for_capability("cap-c") == []


# ---- Tradeoff store --------------------------------------------------------


def test_tradeoff_store_round_trip(tmp_path):
    from jig.tradeoff_store import add_tradeoff, load_ledger

    t = Tradeoff(
        id="no-search",
        decision_summary="No full-text search at bones.",
        deferred=["full-text search"],
        deferred_to="final",
        rationale="Tag filter is sufficient for MVP.",
        capability_ids=["filter-by-tag"],
    )
    add_tradeoff(tmp_path, t)
    loaded = load_ledger(tmp_path)
    assert len(loaded.tradeoffs) == 1
    assert loaded.tradeoffs[0].id == "no-search"


def test_tradeoff_store_upserts_by_id(tmp_path):
    from jig.tradeoff_store import add_tradeoff, load_ledger

    base = Tradeoff(
        id="same-id",
        decision_summary="v1",
        deferred=["x"],
        deferred_to="mvp",
        rationale="r1",
    )
    updated = Tradeoff(
        id="same-id",
        decision_summary="v2",
        deferred=["y"],
        deferred_to="final",
        rationale="r2",
    )
    add_tradeoff(tmp_path, base)
    add_tradeoff(tmp_path, updated)
    loaded = load_ledger(tmp_path)
    assert len(loaded.tradeoffs) == 1
    assert loaded.tradeoffs[0].decision_summary == "v2"


def test_load_ledger_missing_file_returns_empty(tmp_path):
    from jig.tradeoff_store import load_ledger

    ledger = load_ledger(tmp_path)
    assert ledger.tradeoffs == []


# ---- TradeoffComplianceReviewer --------------------------------------------


@pytest.fixture()
def project_with_tradeoff(tmp_path):
    from jig.tradeoff_store import add_tradeoff

    add_tradeoff(
        tmp_path,
        Tradeoff(
            id="no-caching",
            decision_summary="No caching at bones.",
            deferred=["HTTP caching", "TTL"],
            deferred_to="mvp",
            rationale="Premature.",
            capability_ids=["fetch-top-stories"],
        ),
    )
    return tmp_path


def _make_ticket(**kwargs) -> Ticket:
    defaults = dict(
        id="ticket-01",
        title="Implement caching",
        work_type=WorkType.FEATURE,
        created_by="pm",
        layer="bones",
        capability_ids=["fetch-top-stories"],
    )
    defaults.update(kwargs)
    return Ticket(**defaults)


async def test_reviewer_flags_deferred_capability(project_with_tradeoff):
    from jig.reviewers.tradeoff_compliance import TradeoffComplianceReviewer

    ticket = _make_ticket(layer="bones")
    comments = await TradeoffComplianceReviewer().review(
        ticket, project_with_tradeoff
    )
    assert len(comments) == 1
    assert comments[0].type == "deferred-work-reintroduced"
    assert "fetch-top-stories" in comments[0].prose


async def test_reviewer_passes_when_ticket_at_correct_layer(project_with_tradeoff):
    from jig.reviewers.tradeoff_compliance import TradeoffComplianceReviewer

    ticket = _make_ticket(layer="mvp")
    comments = await TradeoffComplianceReviewer().review(
        ticket, project_with_tradeoff
    )
    assert comments == []


async def test_reviewer_no_ops_without_capability_ids(project_with_tradeoff):
    from jig.reviewers.tradeoff_compliance import TradeoffComplianceReviewer

    ticket = _make_ticket(capability_ids=[])
    comments = await TradeoffComplianceReviewer().review(
        ticket, project_with_tradeoff
    )
    assert comments == []


async def test_reviewer_no_ops_with_empty_ledger(tmp_path):
    from jig.reviewers.tradeoff_compliance import TradeoffComplianceReviewer

    ticket = _make_ticket(capability_ids=["fetch-top-stories"])
    comments = await TradeoffComplianceReviewer().review(ticket, tmp_path)
    assert comments == []


async def test_reviewer_flags_never_deferred(tmp_path):
    from jig.tradeoff_store import add_tradeoff
    from jig.reviewers.tradeoff_compliance import TradeoffComplianceReviewer

    add_tradeoff(
        tmp_path,
        Tradeoff(
            id="no-auth-ever",
            decision_summary="No auth in scope, ever.",
            deferred=["login"],
            deferred_to="never",
            rationale="Single-user only.",
            capability_ids=["login"],
        ),
    )
    ticket = _make_ticket(layer="final", capability_ids=["login"])
    comments = await TradeoffComplianceReviewer().review(ticket, tmp_path)
    assert len(comments) == 1


# ---- DoneEnoughBlock on Capability -----------------------------------------


def _base_cap(**kwargs):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    defaults = dict(
        id="fetch-top-stories",
        title="Fetch top stories",
        state=CapabilityState.PLANNED,
        acceptance_criteria=["Stories appear in output"],
        created_at=now,
        last_updated=now,
        state_changed_at=now,
    )
    defaults.update(kwargs)
    return Capability(**defaults)


def test_done_enough_block_minimal():
    block = DoneEnoughBlock(layer="bones", criteria=["Exit 0", "3 lines of output"])
    assert block.layer == "bones"
    assert len(block.criteria) == 2


def test_done_enough_block_requires_criteria():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        DoneEnoughBlock(layer="mvp", criteria=[])


def test_capability_accepts_done_enough():
    cap = _base_cap(
        done_enough=[
            DoneEnoughBlock(layer="bones", criteria=["Renders 3 stories"]),
            DoneEnoughBlock(layer="mvp", criteria=["--min-score filter works"]),
        ]
    )
    assert len(cap.done_enough) == 2
    assert cap.done_enough[0].layer == "bones"


def test_capability_done_enough_defaults_empty():
    cap = _base_cap()
    assert cap.done_enough == []


# ---- GivenWhenThen on Capability and Ticket --------------------------------


def test_given_when_then_minimal():
    gwt = GivenWhenThen(
        given="The API returns 3 stories",
        when="hn-cli top --limit 3 is run",
        then="Three lines appear on stdout",
    )
    assert gwt.given.startswith("The")


def test_given_when_then_rejects_empty_fields():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        GivenWhenThen(given="", when="x", then="y")


def test_capability_accepts_examples():
    cap = _base_cap(
        examples=[
            GivenWhenThen(
                given="The API returns 10 stories",
                when="hn-cli top --limit 3",
                then="Exactly 3 lines on stdout",
            )
        ]
    )
    assert len(cap.examples) == 1


def test_capability_examples_defaults_empty():
    cap = _base_cap()
    assert cap.examples == []


def test_ticket_accepts_examples():
    t = Ticket(
        id="t-001",
        title="Fetch stories",
        work_type=WorkType.FEATURE,
        created_by="pm",
        examples=[
            {"given": "API up", "when": "run top --limit 3", "then": "3 lines"}
        ],
    )
    assert len(t.examples) == 1
    assert t.examples[0]["given"] == "API up"


def test_ticket_examples_default_empty():
    t = Ticket(
        id="t-002",
        title="Some ticket",
        work_type=WorkType.FEATURE,
        created_by="pm",
    )
    assert t.examples == []


# ---- Dispatch: tradeoff-compliance in default sets -------------------------


def test_tradeoff_compliance_in_bones_defaults():
    from jig.reviewers.dispatch import (
        _BONES_DEFAULTS,
        TRADEOFF_COMPLIANCE_REVIEWER_ID,
    )

    assert TRADEOFF_COMPLIANCE_REVIEWER_ID in _BONES_DEFAULTS


def test_tradeoff_compliance_in_mvp_final_defaults():
    from jig.reviewers.dispatch import (
        _MVP_FINAL_DEFAULTS,
        TRADEOFF_COMPLIANCE_REVIEWER_ID,
    )

    assert TRADEOFF_COMPLIANCE_REVIEWER_ID in _MVP_FINAL_DEFAULTS


def test_tradeoff_compliance_in_mechanical_ids():
    from jig.reviewers.dispatch import (
        _MECHANICAL_REVIEWER_IDS,
        TRADEOFF_COMPLIANCE_REVIEWER_ID,
    )

    assert TRADEOFF_COMPLIANCE_REVIEWER_ID in _MECHANICAL_REVIEWER_IDS
