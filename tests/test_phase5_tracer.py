"""Phase 5 tests — tracer schema, spec_loader helpers, graph integration,
and tracer-preservation reviewer.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from jig.schemas.tracer import TracerCovers, TracerSpec
from jig.reviewers.dispatch import TRACER_PRESERVATION_REVIEWER_ID, select_reviewers_for_ticket


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


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
        "n_a_categories": ["behavioral_contracts", "external_dependencies", "ownership"],
    }


def _write_arch(tmp_path: Path, module_ids: list[str]) -> None:
    spec_dir = tmp_path / ".jig" / "spec"
    spec_dir.mkdir(parents=True, exist_ok=True)
    (spec_dir / "architecture.yaml").write_text(
        yaml.dump(
            {"spec_version": 1, "modules": [_module_dict(m) for m in module_ids]},
            allow_unicode=True,
        )
    )


def _write_tracer(tmp_path: Path, tracer_id: str, modules: list[str]) -> Path:
    from jig.spec_loader import save_tracer

    tr = TracerSpec(
        id=tracer_id,
        description=f"Smoke for {tracer_id}",
        command=["echo", "ok"],
        covers=TracerCovers(modules=modules),
    )
    save_tracer(tmp_path, tr)
    return tmp_path / ".jig" / "spec" / "tracers" / f"{tracer_id}.yaml"


def _write_ticket_jsonl(tmp_path: Path, ticket_id: str, modules: list[str]) -> None:
    store_dir = tmp_path / ".jig" / "store"
    store_dir.mkdir(parents=True, exist_ok=True)
    row = {
        "id": ticket_id,
        "title": "t",
        "status": "open",
        "touches": {"modules": modules, "capabilities": [], "epics": []},
    }
    (store_dir / "tickets.jsonl").write_text(json.dumps(row) + "\n")


def _ticket(
    ticket_id: str = "t-1",
    layer: str = "mvp",
    module_id: str | None = "auth",
) -> "Ticket":
    from jig.ticket import Ticket

    return Ticket(
        id=ticket_id,
        title="test",
        work_type="feature",
        created_by="test",
        layer=layer,
        module_id=module_id,
    )


# ---------------------------------------------------------------------------
# TracerSpec schema
# ---------------------------------------------------------------------------


def test_tracer_spec_minimal() -> None:
    tr = TracerSpec(
        id="smoke",
        description="run smoke",
        command=["pytest", "tests/smoke"],
    )
    assert tr.id == "smoke"
    assert tr.covers.modules == []
    assert tr.timeout_seconds == 60


def test_tracer_spec_full_round_trip() -> None:
    tr = TracerSpec(
        id="hn-cli-smoke",
        description="HN smoke",
        command=["python", "-m", "pytest"],
        covers=TracerCovers(
            modules=["api-client", "cli-frontend"],
            capabilities=["fetch-top-stories"],
            exposed_apis=["api-client:get_top"],
            emitted_events=["api-client:stories-fetched"],
        ),
        bones_ticket_id="t-001",
        timeout_seconds=120,
    )
    dumped = tr.model_dump(mode="json")
    loaded = TracerSpec.model_validate(dumped)
    assert loaded.covers.modules == ["api-client", "cli-frontend"]
    assert loaded.bones_ticket_id == "t-001"


def test_tracer_spec_invalid_id_rejected() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        TracerSpec(id="Bad-ID", description="x", command=["x"])


def test_tracer_spec_empty_command_rejected() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        TracerSpec(id="smoke", description="x", command=[])


# ---------------------------------------------------------------------------
# spec_loader helpers
# ---------------------------------------------------------------------------


def test_save_and_load_tracer(tmp_path: Path) -> None:
    from jig.spec_loader import load_tracer, save_tracer

    tr = TracerSpec(
        id="my-smoke",
        description="My smoke",
        command=["echo", "ok"],
        covers=TracerCovers(modules=["auth"]),
    )
    save_tracer(tmp_path, tr)
    loaded = load_tracer(tmp_path, "my-smoke")
    assert loaded.id == "my-smoke"
    assert loaded.covers.modules == ["auth"]


def test_load_tracer_missing_raises(tmp_path: Path) -> None:
    from jig.spec_loader import load_tracer

    with pytest.raises(FileNotFoundError):
        load_tracer(tmp_path, "nonexistent")


def test_load_all_tracers_empty_dir(tmp_path: Path) -> None:
    from jig.spec_loader import load_all_tracers

    result = load_all_tracers(tmp_path)
    assert result == []


def test_load_all_tracers_returns_all(tmp_path: Path) -> None:
    from jig.spec_loader import load_all_tracers

    _write_tracer(tmp_path, "smoke-a", ["auth"])
    _write_tracer(tmp_path, "smoke-b", ["billing"])
    result = load_all_tracers(tmp_path)
    ids = {tr.id for tr in result}
    assert ids == {"smoke-a", "smoke-b"}


def test_load_all_tracers_skips_corrupt(tmp_path: Path) -> None:
    from jig.spec_loader import load_all_tracers

    tracer_dir = tmp_path / ".jig" / "spec" / "tracers"
    tracer_dir.mkdir(parents=True)
    (tracer_dir / "bad.yaml").write_text("not: valid: tracer: data")
    _write_tracer(tmp_path, "good-one", ["auth"])
    result = load_all_tracers(tmp_path)
    assert len(result) == 1
    assert result[0].id == "good-one"


# ---------------------------------------------------------------------------
# build_graph: tracer nodes
# ---------------------------------------------------------------------------


def test_build_graph_includes_tracer_node(tmp_path: Path) -> None:
    from jig.graph.derive import build_graph

    _write_arch(tmp_path, ["auth"])
    _write_tracer(tmp_path, "auth-smoke", ["auth"])

    graph = build_graph(tmp_path)
    node_ids = {n.id for n in graph.nodes}
    assert "tracer:auth-smoke" in node_ids


def test_build_graph_tracer_covers_edge(tmp_path: Path) -> None:
    from jig.graph.derive import build_graph

    _write_arch(tmp_path, ["auth"])
    _write_tracer(tmp_path, "auth-smoke", ["auth"])

    graph = build_graph(tmp_path)
    covers_edges = {
        (e.src, e.dst)
        for e in graph.edges
        if e.kind == "covers" and e.src == "tracer:auth-smoke"
    }
    assert ("tracer:auth-smoke", "module:auth") in covers_edges


def test_build_graph_no_tracers_dir(tmp_path: Path) -> None:
    from jig.graph.derive import build_graph

    _write_arch(tmp_path, ["auth"])
    graph = build_graph(tmp_path)
    tracer_nodes = [n for n in graph.nodes if n.kind == "tracer"]
    assert tracer_nodes == []


def test_ticket_impact_exercised_tracers(tmp_path: Path) -> None:
    from jig.graph.derive import build_graph, ticket_impact

    _write_arch(tmp_path, ["auth"])
    _write_tracer(tmp_path, "auth-smoke", ["auth"])
    _write_ticket_jsonl(tmp_path, "t-1", ["auth"])

    graph = build_graph(tmp_path)
    impact = ticket_impact(graph, "t-1", depth=1)
    tracer_ids = {n.id for n in impact.exercised_tracers}
    assert "tracer:auth-smoke" in tracer_ids


# ---------------------------------------------------------------------------
# tracer-preservation reviewer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tracer_preservation_no_arch_returns_empty(tmp_path: Path) -> None:
    from jig.reviewers.tracer_preservation import TracerPreservationReviewer

    ticket = _ticket()
    result = await TracerPreservationReviewer().review(ticket, tmp_path)
    assert result == []


@pytest.mark.asyncio
async def test_tracer_preservation_no_tracers_returns_empty(tmp_path: Path) -> None:
    from jig.reviewers.tracer_preservation import TracerPreservationReviewer

    _write_arch(tmp_path, ["auth"])
    ticket = _ticket()
    result = await TracerPreservationReviewer().review(ticket, tmp_path)
    assert result == []


@pytest.mark.asyncio
async def test_tracer_preservation_no_overlap_returns_empty(tmp_path: Path) -> None:
    from jig.reviewers.tracer_preservation import TracerPreservationReviewer

    _write_arch(tmp_path, ["auth", "billing"])
    _write_tracer(tmp_path, "billing-smoke", ["billing"])
    _write_ticket_jsonl(tmp_path, "t-1", ["auth"])

    ticket = _ticket(ticket_id="t-1", module_id="auth")
    result = await TracerPreservationReviewer().review(ticket, tmp_path)
    assert result == []


@pytest.mark.asyncio
async def test_tracer_preservation_overlap_emits_comment(tmp_path: Path) -> None:
    from jig.reviewers.comment import ReviewerCommentType
    from jig.reviewers.tracer_preservation import TracerPreservationReviewer

    _write_arch(tmp_path, ["auth"])
    _write_tracer(tmp_path, "auth-smoke", ["auth"])
    _write_ticket_jsonl(tmp_path, "t-1", ["auth"])

    ticket = _ticket(ticket_id="t-1", module_id="auth")
    result = await TracerPreservationReviewer().review(ticket, tmp_path)

    assert len(result) == 1
    assert result[0].type == ReviewerCommentType.TRACER_PRESERVATION_RISK.value
    assert "auth-smoke" in result[0].prose
    assert "auth" in result[0].prose


@pytest.mark.asyncio
async def test_tracer_preservation_multiple_tracers_overlap(tmp_path: Path) -> None:
    from jig.reviewers.tracer_preservation import TracerPreservationReviewer

    _write_arch(tmp_path, ["auth", "billing"])
    _write_tracer(tmp_path, "auth-smoke", ["auth"])
    _write_tracer(tmp_path, "full-smoke", ["auth", "billing"])
    _write_ticket_jsonl(tmp_path, "t-1", ["auth"])

    ticket = _ticket(ticket_id="t-1", module_id="auth")
    result = await TracerPreservationReviewer().review(ticket, tmp_path)

    triggered = {c.contract_uri for c in result}
    assert "project://spec/tracers/auth-smoke" in triggered
    assert "project://spec/tracers/full-smoke" in triggered


# ---------------------------------------------------------------------------
# dispatch wiring
# ---------------------------------------------------------------------------


def test_tracer_preservation_reviewer_in_mvp_defaults() -> None:
    ticket = _ticket(layer="mvp")
    selected = select_reviewers_for_ticket(ticket)
    assert TRACER_PRESERVATION_REVIEWER_ID in selected


def test_tracer_preservation_reviewer_in_final_defaults() -> None:
    ticket = _ticket(layer="final")
    selected = select_reviewers_for_ticket(ticket)
    assert TRACER_PRESERVATION_REVIEWER_ID in selected


def test_tracer_preservation_reviewer_not_in_bones() -> None:
    ticket = _ticket(layer="bones")
    selected = select_reviewers_for_ticket(ticket)
    assert TRACER_PRESERVATION_REVIEWER_ID not in selected


@pytest.mark.asyncio
async def test_tracer_preservation_excluded_at_per_commit(tmp_path: Path) -> None:
    from jig.reviewers.dispatch import dispatch_for_cadence

    _write_arch(tmp_path, ["auth"])
    _write_tracer(tmp_path, "auth-smoke", ["auth"])
    _write_ticket_jsonl(tmp_path, "t-1", ["auth"])

    ticket = _ticket(ticket_id="t-1", layer="mvp", module_id="auth")
    result = await dispatch_for_cadence(
        ticket,
        tmp_path,
        "per_commit",
    )
    assert TRACER_PRESERVATION_REVIEWER_ID not in result
