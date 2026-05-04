"""Planner PM MCP tool handler (Track F MVP).

Mirrors the structure of ``tests/test_sa_mcp.py``. The Planner is the
first MVP-tier v2 agent; the finalize handler is one-shot, validates
the ``BuildPlan`` schema + plan-shape rules, writes
``.jig/plan/build-plan.yaml``, posts a Handoff to the Coordinator
phase, and resolves the plan ticket.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from jig.intent import Intent
from jig.planner_pm_mcp import (
    PLANNER_NEXT_PHASE,
    PLANNER_TICKET_ID,
    handle_plan_finalize,
)
from jig.schemas.plan import (
    BuildPlan,
    Epic,
    EpicLayers,
    LayerStatus,
    OrderingRule,
)
from jig.spec_loader import build_plan_path, load_build_plan
from jig.store.bus import MessageBus
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.ticket import Ticket, TicketStatus, WorkType


# ---- fixtures -------------------------------------------------------------


@pytest.fixture
async def wired(tmp_path: Path):
    spec_dir = tmp_path / ".jig" / "spec"
    spec_dir.mkdir(parents=True)
    tickets = TicketStore(tmp_path / "tickets.jsonl")
    threads = ThreadStore(tmp_path / "comments.jsonl")
    bus = MessageBus(tmp_path / "messages.jsonl")
    for s in (tickets, threads, bus):
        await s.load()
    plan_ticket = Ticket(
        id=PLANNER_TICKET_ID,
        work_type=WorkType.BRIEF,
        title="Planner — build plan",
        created_by="cli",
    )
    await tickets.create(plan_ticket)
    return {
        "tickets": tickets,
        "threads": threads,
        "bus": bus,
        "project_path": tmp_path,
    }


def _intent_dict(
    *,
    problem: str = "Validate bones spine end-to-end",
    simplest: str = "One epic, one bones ticket, one capability",
) -> dict:
    """Minimum-viable Intent — required on every Epic."""
    return {
        "problem": problem,
        "simplest_solution": simplest,
        "complications_considered": {
            "scale": None,
            "concurrency": None,
            "failure_modes": None,
            "cross_cutting": None,
        },
    }


def _epic_dict(
    *,
    epic_id: str = "catalog-ingest",
    suite: str = "catalog",
    module: str = "catalog-ingest",
    bones_tickets: list[str] | None = None,
    mvp_tickets: list[str] | None = None,
    final_tickets: list[str] | None = None,
) -> dict:
    """Minimum-viable Epic dict — every epic carries Intent + layers.

    ``None`` (the default) means "fill with the default tracer-bullet
    ticket id for this epic"; an explicit empty list means "no
    tickets in this layer." Distinguishing the two matters for the
    no-bones-layer-populated rejection test.
    """
    return {
        "id": epic_id,
        "title": f"{epic_id} epic",
        "suite": suite,
        "modules": [module],
        "layers": {
            "bones": {
                "status": "not_started",
                "tickets": (
                    [f"tb-{epic_id}"] if bones_tickets is None else bones_tickets
                ),
            },
            "mvp": {
                "status": "not_started",
                "tickets": [] if mvp_tickets is None else mvp_tickets,
            },
            "final": {
                "status": "not_started",
                "tickets": [] if final_tickets is None else final_tickets,
            },
        },
        "risks_addressed": [],
        "intent": _intent_dict(),
    }


def _plan_dict(
    *,
    project: str = "bones-walking-skeleton",
    epics: list[dict] | None = None,
    ordering_rule: str = "bones_first",
) -> dict:
    """Minimum-viable BuildPlan dict — one epic, one bones ticket."""
    return {
        "spec_version": 1,
        "project": project,
        "revision": 1,
        "ordering_rule": ordering_rule,
        "epics": epics if epics is not None else [_epic_dict()],
        "stalled": [],
        "open_questions": [],
    }


# ---- happy path ----------------------------------------------------------


@pytest.mark.asyncio
async def test_plan_finalize_writes_build_plan_yaml(wired):
    await handle_plan_finalize(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        plan=_plan_dict(),
        author="planner-pm",
    )
    plan_file = build_plan_path(wired["project_path"])
    assert plan_file.is_file()
    data = yaml.safe_load(plan_file.read_text())
    assert data["project"] == "bones-walking-skeleton"
    assert data["ordering_rule"] == OrderingRule.BONES_FIRST.value
    assert data["epics"][0]["id"] == "catalog-ingest"
    assert data["epics"][0]["layers"]["bones"]["tickets"] == [
        "tb-catalog-ingest"
    ]
    # Intent round-trips through the writer.
    assert data["epics"][0]["intent"]["problem"]


@pytest.mark.asyncio
async def test_plan_finalize_round_trips_through_loader(wired):
    """The artifact the Planner writes loads back as the same model."""
    await handle_plan_finalize(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        plan=_plan_dict(),
        author="planner-pm",
    )
    loaded = load_build_plan(wired["project_path"])
    assert isinstance(loaded, BuildPlan)
    assert loaded.project == "bones-walking-skeleton"
    assert loaded.ordering_rule == OrderingRule.BONES_FIRST


@pytest.mark.asyncio
async def test_plan_finalize_resolves_ticket(wired):
    await handle_plan_finalize(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        plan=_plan_dict(),
        author="planner-pm",
    )
    t = await wired["tickets"].get(PLANNER_TICKET_ID)
    assert t is not None
    assert t.status == TicketStatus.RESOLVED


@pytest.mark.asyncio
async def test_plan_finalize_emits_handoff_to_coordinator(wired):
    await handle_plan_finalize(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        plan=_plan_dict(),
        author="planner-pm",
    )
    entries = await wired["threads"].for_ticket(PLANNER_TICKET_ID)
    handoffs = [e for e in entries if e.kind == "handoff"]
    assert len(handoffs) == 1
    h = handoffs[0]
    assert h.phase == PLANNER_NEXT_PHASE == "coordinator"
    assert ".jig/plan/build-plan.yaml" in h.outputs


@pytest.mark.asyncio
async def test_plan_finalize_accepts_pydantic_instance_directly(wired):
    """Idempotent coercion — a BuildPlan instance round-trips without re-validate."""
    plan = BuildPlan(
        project="direct",
        epics=[
            Epic(
                id="solo",
                title="Solo epic",
                suite="catalog",
                modules=["catalog-ingest"],
                layers=EpicLayers(
                    bones=LayerStatus(tickets=["tb-solo"]),
                ),
                intent=Intent(
                    problem="prove instance path",
                    simplest_solution="pass the model in directly",
                ),
            ),
        ],
    )
    await handle_plan_finalize(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        plan=plan,
        author="planner-pm",
    )
    loaded = load_build_plan(wired["project_path"])
    assert loaded.project == "direct"


# ---- validation rejections ----------------------------------------------


@pytest.mark.asyncio
async def test_plan_finalize_rejects_non_dict_plan(wired):
    with pytest.raises(ValueError, match="plan must be a dict or BuildPlan"):
        await handle_plan_finalize(
            tickets=wired["tickets"],
            threads=wired["threads"],
            bus=wired["bus"],
            project_path=wired["project_path"],
            plan="not-a-dict",
            author="planner-pm",
        )
    assert not build_plan_path(wired["project_path"]).exists()


@pytest.mark.asyncio
async def test_plan_finalize_rejects_missing_project_field(wired):
    """Pydantic-required field; surface the schema error verbatim."""
    bad = _plan_dict()
    del bad["project"]
    with pytest.raises(ValueError, match="plan does not validate"):
        await handle_plan_finalize(
            tickets=wired["tickets"],
            threads=wired["threads"],
            bus=wired["bus"],
            project_path=wired["project_path"],
            plan=bad,
            author="planner-pm",
        )
    assert not build_plan_path(wired["project_path"]).exists()


@pytest.mark.asyncio
async def test_plan_finalize_rejects_missing_epic_intent(wired):
    """Every epic must carry Intent — the load-bearing v2 discipline."""
    bad = _plan_dict()
    del bad["epics"][0]["intent"]
    with pytest.raises(ValueError, match="plan does not validate"):
        await handle_plan_finalize(
            tickets=wired["tickets"],
            threads=wired["threads"],
            bus=wired["bus"],
            project_path=wired["project_path"],
            plan=bad,
            author="planner-pm",
        )


@pytest.mark.asyncio
async def test_plan_finalize_rejects_duplicate_ticket_ids_across_epics(wired):
    """A ticket id may not appear in two different epics.

    Block A.2 hoists this gate to the ``BuildPlan`` schema layer; the
    handler's coercion step now rejects the duplicate before the
    explicit handler-level check fires. The schema's error message
    names the colliding (epic, layer) anchors so the test asserts on
    that text instead of the handler's wording.
    """
    bad = _plan_dict(
        epics=[
            _epic_dict(epic_id="catalog-ingest", bones_tickets=["tb-shared"]),
            _epic_dict(
                epic_id="categorization",
                suite="catalog",
                module="categorization",
                bones_tickets=["tb-shared"],
            ),
        ]
    )
    with pytest.raises(ValueError, match="multiple .epic, layer. slots"):
        await handle_plan_finalize(
            tickets=wired["tickets"],
            threads=wired["threads"],
            bus=wired["bus"],
            project_path=wired["project_path"],
            plan=bad,
            author="planner-pm",
        )
    assert not build_plan_path(wired["project_path"]).exists()


@pytest.mark.asyncio
async def test_plan_finalize_rejects_duplicate_ticket_ids_across_layers(wired):
    """A ticket id may not span bones + mvp inside one epic either.

    Block A.2 schema-level enforcement; see the across-epics test above
    for the rationale.
    """
    bad = _plan_dict(
        epics=[
            _epic_dict(
                bones_tickets=["t-shared"],
                mvp_tickets=["t-shared"],
            ),
        ]
    )
    with pytest.raises(ValueError, match="multiple .epic, layer. slots"):
        await handle_plan_finalize(
            tickets=wired["tickets"],
            threads=wired["threads"],
            bus=wired["bus"],
            project_path=wired["project_path"],
            plan=bad,
            author="planner-pm",
        )


@pytest.mark.asyncio
async def test_plan_finalize_rejects_no_bones_layer_populated(wired):
    """bones_first ordering needs at least one bones ticket to dispatch."""
    bad = _plan_dict(
        epics=[
            _epic_dict(bones_tickets=[], mvp_tickets=["t-mvp-only"]),
        ]
    )
    with pytest.raises(ValueError, match="no bones-layer tickets"):
        await handle_plan_finalize(
            tickets=wired["tickets"],
            threads=wired["threads"],
            bus=wired["bus"],
            project_path=wired["project_path"],
            plan=bad,
            author="planner-pm",
        )
    assert not build_plan_path(wired["project_path"]).exists()


@pytest.mark.asyncio
async def test_plan_finalize_rejects_empty_epics_list(wired):
    """No epics at all → no bones tickets either; surface as bones-empty."""
    bad = _plan_dict(epics=[])
    with pytest.raises(ValueError, match="no bones-layer tickets"):
        await handle_plan_finalize(
            tickets=wired["tickets"],
            threads=wired["threads"],
            bus=wired["bus"],
            project_path=wired["project_path"],
            plan=bad,
            author="planner-pm",
        )


@pytest.mark.asyncio
async def test_plan_finalize_rejects_empty_string_epic_id(wired):
    """Empty epic id fails Pydantic min_length before plan-shape rules run."""
    bad = _plan_dict()
    bad["epics"][0]["id"] = ""
    with pytest.raises(ValueError, match="plan does not validate"):
        await handle_plan_finalize(
            tickets=wired["tickets"],
            threads=wired["threads"],
            bus=wired["bus"],
            project_path=wired["project_path"],
            plan=bad,
            author="planner-pm",
        )


# ---- idempotency ---------------------------------------------------------


@pytest.mark.asyncio
async def test_plan_finalize_idempotent_overwrite(wired):
    """Re-finalize replaces the plan; the artifact reflects the latest call.

    The Planner may legitimately re-run on SA changes (Final scope) or
    after operator revisions. Each call must replace cleanly.
    """
    await handle_plan_finalize(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        plan=_plan_dict(),
        author="planner-pm",
    )
    # Reactivate so the second call's resolve-after-handoff doesn't no-op.
    await wired["tickets"].update(
        PLANNER_TICKET_ID, status=TicketStatus.IN_PROGRESS
    )
    second = _plan_dict(project="bones-walking-skeleton")
    second["revision"] = 2
    second["epics"][0]["title"] = "Refined catalog-ingest epic"
    await handle_plan_finalize(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        plan=second,
        author="planner-pm",
    )
    data = yaml.safe_load(build_plan_path(wired["project_path"]).read_text())
    assert data["revision"] == 2
    assert data["epics"][0]["title"] == "Refined catalog-ingest epic"


# ---- multi-epic shapes the Planner emits in MVP --------------------------


@pytest.mark.asyncio
async def test_plan_finalize_accepts_multi_epic_plan(wired):
    """The MVP-typical shape: one epic per module, layered into bones/MVP."""
    plan = _plan_dict(
        epics=[
            _epic_dict(
                epic_id="catalog-ingest",
                suite="catalog",
                module="catalog-ingest",
                bones_tickets=["tb-catalog-ingest"],
                mvp_tickets=["t-shopify-oauth", "t-csv-parser"],
                final_tickets=["t-rate-limit-handling"],
            ),
            _epic_dict(
                epic_id="categorization",
                suite="catalog",
                module="categorization",
                bones_tickets=["tb-categorization"],
                mvp_tickets=["t-seorank-client"],
                final_tickets=[],
            ),
        ]
    )
    await handle_plan_finalize(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        plan=plan,
        author="planner-pm",
    )
    loaded = load_build_plan(wired["project_path"])
    assert len(loaded.epics) == 2
    assert {e.id for e in loaded.epics} == {"catalog-ingest", "categorization"}
