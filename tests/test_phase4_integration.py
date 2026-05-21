"""Phase 4 integration tests.

Covers:
- graph_context.py: build_graph_context returns module contracts text
- dispatch.py: _touches_consumed_interface, promote_dev_tier
- dispatch.py: contract-test-coverage reviewer wiring (cadence guard)
- analytics/events.py: TicketGraphImpact round-trips
- quartermaster.py: _pattern_complex_tickets + calibration
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from jig.analytics.events import TicketGraphImpact
from jig.analytics.store import AnalyticsStore
from jig.quartermaster import (
    Quartermaster,
    PatternCalibration,
    _DEFAULT_COMPLEX_TICKET_BOUNDARY_THRESHOLD,
)
from jig.reviewers.dispatch import (
    CONTRACT_TEST_COVERAGE_REVIEWER_ID,
    promote_dev_tier,
    select_reviewers_for_ticket,
)
from jig.ticket import Ticket
from tests._test_ticket import TICKET_AC_PLACEHOLDER

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _ticket(
    ticket_id: str = "t1",
    layer: str = "mvp",
    module_id: str | None = "auth",
    dev_tier: str | None = None,
) -> Ticket:
    return Ticket(
        id=ticket_id,
        title="test",
        work_type="feature",
        created_by="test",
        layer=layer,
        module_id=module_id,
        dev_tier=dev_tier,
        description=TICKET_AC_PLACEHOLDER,
    )


def _minimal_intent() -> dict:
    return {
        "problem": "Need to do X.",
        "simplest_solution": "One function.",
        "complications_considered": {},
    }


def _module_dict(mid: str) -> dict:
    return {
        "id": mid,
        "title": mid.title(),
        "summary": f"Module {mid}.",
        "intent": _minimal_intent(),
        "n_a_categories": [
            "behavioral_contracts",
            "external_dependencies",
            "ownership",
        ],
    }


def _arch_yaml(tmp_path: Path, module_ids: list[str]) -> None:
    spec_dir = tmp_path / ".jig" / "spec"
    spec_dir.mkdir(parents=True, exist_ok=True)
    (spec_dir / "architecture.yaml").write_text(
        yaml.dump(
            {"spec_version": 1, "modules": [_module_dict(m) for m in module_ids]},
            allow_unicode=True,
        )
    )


def _contracts_yaml(tmp_path: Path, module_id: str, content: str) -> None:
    mod_dir = tmp_path / ".jig" / "spec" / "modules" / module_id
    mod_dir.mkdir(parents=True, exist_ok=True)
    (mod_dir / "contracts.yaml").write_text(content)


async def _make_analytics(tmp_path: Path, events: list) -> AnalyticsStore:
    path = tmp_path / "analytics.jsonl"
    store = AnalyticsStore(path)
    await store.load()
    for ev in events:
        await store.append(ev)
    return store


# ---------------------------------------------------------------------------
# graph_context
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_graph_context_no_arch_returns_empty(tmp_path: Path) -> None:
    from jig.graph_context import build_graph_context

    ticket = _ticket()
    result = await build_graph_context(ticket, tmp_path, depth=1)
    assert result == ""


@pytest.mark.asyncio
async def test_build_graph_context_no_module_touches_returns_empty(
    tmp_path: Path,
) -> None:
    from jig.graph_context import build_graph_context

    _arch_yaml(tmp_path, ["auth"])
    # ticket has no touches edge in the JSONL → no touched modules
    ticket = _ticket(ticket_id="t-999")
    result = await build_graph_context(ticket, tmp_path, depth=1)
    assert result == ""


@pytest.mark.asyncio
async def test_build_graph_context_returns_contracts_section(
    tmp_path: Path,
) -> None:
    from jig.graph_context import build_graph_context

    _arch_yaml(tmp_path, ["auth"])
    _contracts_yaml(
        tmp_path,
        "auth",
        "spec_version: 1\nmodule: auth\nbehavioral_contracts:\n  - id: login\n    summary: handles login\n    musts: []\n    must_nots: []\n",
    )

    # Write a ticket JSONL with a touches edge
    store_dir = tmp_path / ".jig" / "store"
    store_dir.mkdir(parents=True, exist_ok=True)
    (store_dir / "tickets.jsonl").write_text(
        '{"id": "t-1", "title": "t", "status": "open", "touches": '
        '{"modules": ["auth"], "capabilities": [], "epics": []}}\n'
    )

    ticket = _ticket(ticket_id="t-1", module_id="auth")
    result = await build_graph_context(ticket, tmp_path, depth=1)

    assert "## Graph Context" in result
    assert "contracts: auth" in result
    assert "login" in result


@pytest.mark.asyncio
async def test_build_graph_context_missing_contracts_file_skipped(
    tmp_path: Path,
) -> None:
    from jig.graph_context import build_graph_context

    _arch_yaml(tmp_path, ["auth"])
    # No contracts.yaml written

    store_dir = tmp_path / ".jig" / "store"
    store_dir.mkdir(parents=True, exist_ok=True)
    (store_dir / "tickets.jsonl").write_text(
        '{"id": "t-1", "title": "t", "status": "open", "touches": '
        '{"modules": ["auth"], "capabilities": [], "epics": []}}\n'
    )

    ticket = _ticket(ticket_id="t-1")
    result = await build_graph_context(ticket, tmp_path, depth=1)
    # No contracts → empty result (no sections to emit)
    assert result == ""


# ---------------------------------------------------------------------------
# promote_dev_tier
# ---------------------------------------------------------------------------


def _write_ticket_jsonl(tmp_path: Path, ticket_id: str, modules: list[str]) -> None:
    import json as _json

    store_dir = tmp_path / ".jig" / "store"
    store_dir.mkdir(parents=True, exist_ok=True)
    row = {
        "id": ticket_id,
        "title": "t",
        "status": "open",
        "touches": {"modules": modules, "capabilities": [], "epics": []},
    }
    (store_dir / "tickets.jsonl").write_text(_json.dumps(row) + "\n")


def _arch_with_two_modules(tmp_path: Path) -> None:
    _arch_yaml(
        tmp_path,
        ["auth", "billing"],
    )


def test_promote_dev_tier_no_arch_returns_none(tmp_path: Path) -> None:
    ticket = _ticket()
    result = promote_dev_tier(ticket, tmp_path)
    assert result is None


def test_promote_dev_tier_no_boundaries_returns_none(tmp_path: Path) -> None:
    _arch_with_two_modules(tmp_path)
    _write_ticket_jsonl(tmp_path, "t-1", ["auth"])
    ticket = _ticket(ticket_id="t-1", module_id="auth")
    result = promote_dev_tier(ticket, tmp_path)
    assert result is None


def test_promote_dev_tier_single_boundary_promotes_to_senior(tmp_path: Path) -> None:
    _arch_with_two_modules(tmp_path)
    _write_ticket_jsonl(tmp_path, "t-1", ["auth", "billing"])
    ticket = _ticket(ticket_id="t-1", module_id="auth")
    result = promote_dev_tier(ticket, tmp_path)
    assert result == "senior"


def test_promote_dev_tier_three_boundaries_promotes_to_sa(tmp_path: Path) -> None:
    mods = ["a", "b", "c", "d"]
    _arch_yaml(tmp_path, mods)
    # Four modules → 3 crossed boundaries
    _write_ticket_jsonl(tmp_path, "t-1", mods)
    ticket = _ticket(ticket_id="t-1", module_id="a")
    result = promote_dev_tier(ticket, tmp_path)
    assert result == "sa"


def test_promote_dev_tier_no_demotion(tmp_path: Path) -> None:
    # Ticket already at SA — no promotion returned
    _arch_with_two_modules(tmp_path)
    _write_ticket_jsonl(tmp_path, "t-1", ["auth", "billing"])
    ticket = _ticket(ticket_id="t-1", module_id="auth", dev_tier="sa")
    result = promote_dev_tier(ticket, tmp_path)
    assert result is None


def test_promote_dev_tier_upgrades_senior_to_sa(tmp_path: Path) -> None:
    mods = ["a", "b", "c", "d"]
    _arch_yaml(tmp_path, mods)
    _write_ticket_jsonl(tmp_path, "t-1", mods)
    ticket = _ticket(ticket_id="t-1", module_id="a", dev_tier="senior")
    result = promote_dev_tier(ticket, tmp_path)
    assert result == "sa"


# ---------------------------------------------------------------------------
# contract-test-coverage reviewer cadence
# ---------------------------------------------------------------------------


def test_contract_coverage_reviewer_in_mvp_defaults() -> None:
    ticket = _ticket(layer="mvp")
    selected = select_reviewers_for_ticket(ticket)
    assert CONTRACT_TEST_COVERAGE_REVIEWER_ID in selected


def test_contract_coverage_reviewer_in_final_defaults() -> None:
    ticket = _ticket(layer="final")
    selected = select_reviewers_for_ticket(ticket)
    assert CONTRACT_TEST_COVERAGE_REVIEWER_ID in selected


def test_contract_coverage_reviewer_not_in_bones_defaults() -> None:
    ticket = _ticket(layer="bones")
    selected = select_reviewers_for_ticket(ticket)
    assert CONTRACT_TEST_COVERAGE_REVIEWER_ID not in selected


# ---------------------------------------------------------------------------
# TicketGraphImpact analytics event
# ---------------------------------------------------------------------------


def test_ticket_graph_impact_round_trips() -> None:
    ev = TicketGraphImpact(
        ticket_id="T-42",
        crossed_boundaries=3,
        touched_node_count=5,
        consumer_count=2,
        exercised_tracer_count=1,
    )
    dumped = ev.model_dump(mode="json")
    from jig.analytics.events import AnalyticsEvent
    from pydantic import TypeAdapter

    ta = TypeAdapter(AnalyticsEvent)
    loaded = ta.validate_python(dumped)
    assert isinstance(loaded, TicketGraphImpact)
    assert loaded.ticket_id == "T-42"
    assert loaded.crossed_boundaries == 3
    assert loaded.touched_node_count == 5


def test_ticket_graph_impact_defaults() -> None:
    ev = TicketGraphImpact(ticket_id="t-1")
    assert ev.crossed_boundaries == 0
    assert ev.touched_node_count == 0
    assert ev.consumer_count == 0
    assert ev.exercised_tracer_count == 0
    assert ev.kind == "ticket_graph_impact"


def test_ticket_graph_impact_rejects_negative_counts() -> None:
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        TicketGraphImpact(ticket_id="t-1", crossed_boundaries=-1)


# ---------------------------------------------------------------------------
# Quartermaster: _pattern_complex_tickets
# ---------------------------------------------------------------------------


def _impact_event(
    ticket_id: str,
    crossed_boundaries: int,
    ev_id: str | None = None,
) -> TicketGraphImpact:
    ev = TicketGraphImpact(
        ticket_id=ticket_id,
        crossed_boundaries=crossed_boundaries,
        touched_node_count=2,
    )
    if ev_id is not None:
        object.__setattr__(ev, "id", ev_id)
    return ev


@pytest.mark.asyncio
async def test_qm_no_complex_tickets_no_pattern(tmp_path: Path) -> None:
    events = [
        _impact_event("T-1", 1),
        _impact_event("T-2", 2),
    ]
    store = await _make_analytics(tmp_path, events)
    qm = Quartermaster(store)
    briefing = await qm.briefing()
    kinds = [p.kind for p in briefing.notable_patterns]
    assert "tickets_crossing_many_boundaries" not in kinds


@pytest.mark.asyncio
async def test_qm_complex_ticket_at_threshold_fires(tmp_path: Path) -> None:
    threshold = _DEFAULT_COMPLEX_TICKET_BOUNDARY_THRESHOLD
    events = [_impact_event("T-heavy", threshold)]
    store = await _make_analytics(tmp_path, events)
    qm = Quartermaster(store)
    briefing = await qm.briefing()
    matching = [
        p
        for p in briefing.notable_patterns
        if p.kind == "tickets_crossing_many_boundaries"
    ]
    assert len(matching) == 1
    assert "T-heavy" in matching[0].description


@pytest.mark.asyncio
async def test_qm_complex_tickets_lists_all_offenders(tmp_path: Path) -> None:
    threshold = _DEFAULT_COMPLEX_TICKET_BOUNDARY_THRESHOLD
    events = [
        _impact_event("T-1", threshold),
        _impact_event("T-2", threshold + 1),
        _impact_event("T-ok", threshold - 1),
    ]
    store = await _make_analytics(tmp_path, events)
    qm = Quartermaster(store)
    briefing = await qm.briefing()
    matching = [
        p
        for p in briefing.notable_patterns
        if p.kind == "tickets_crossing_many_boundaries"
    ]
    assert len(matching) == 1
    assert "T-1" in matching[0].description
    assert "T-2" in matching[0].description
    assert "T-ok" not in matching[0].description
    assert len(matching[0].evidence_event_ids) == 2


@pytest.mark.asyncio
async def test_qm_complex_tickets_below_threshold_not_flagged(tmp_path: Path) -> None:
    threshold = _DEFAULT_COMPLEX_TICKET_BOUNDARY_THRESHOLD
    events = [_impact_event("T-1", threshold - 1)]
    store = await _make_analytics(tmp_path, events)
    qm = Quartermaster(store)
    briefing = await qm.briefing()
    assert not any(
        p.kind == "tickets_crossing_many_boundaries" for p in briefing.notable_patterns
    )


@pytest.mark.asyncio
async def test_qm_complex_tickets_custom_threshold(tmp_path: Path) -> None:
    events = [_impact_event("T-1", 5)]
    store = await _make_analytics(tmp_path, events)
    qm = Quartermaster(store, complex_ticket_boundary_threshold=6)
    briefing = await qm.briefing()
    assert not any(
        p.kind == "tickets_crossing_many_boundaries" for p in briefing.notable_patterns
    )


@pytest.mark.asyncio
async def test_qm_complex_tickets_calibration_raises_threshold(
    tmp_path: Path,
) -> None:
    """Calibrated threshold > crossed_boundaries → no pattern fires."""

    events = [_impact_event("T-1", 3)]  # threshold=3, hits by default
    store = await _make_analytics(tmp_path, events)
    calibration = PatternCalibration(thresholds={"tickets_crossing_many_boundaries": 4})
    qm = Quartermaster(store, calibration=calibration)
    briefing = await qm.briefing()
    assert not any(
        p.kind == "tickets_crossing_many_boundaries" for p in briefing.notable_patterns
    )


@pytest.mark.asyncio
async def test_qm_complex_tickets_in_recommendations(tmp_path: Path) -> None:
    threshold = _DEFAULT_COMPLEX_TICKET_BOUNDARY_THRESHOLD
    events = [_impact_event("T-heavy", threshold)]
    store = await _make_analytics(tmp_path, events)
    qm = Quartermaster(store, recommendation_limit=5)
    briefing = await qm.briefing()
    assert any(
        "tickets_crossing_many_boundaries" in r
        for r in briefing.attention_recommendations
    )
