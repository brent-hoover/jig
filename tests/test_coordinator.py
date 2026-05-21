"""Coordinator PM bones dispatch (Track F4, bones).

Bones scope: ``Coordinator.materialize_ready_tickets`` reads the
build plan, creates one ``Ticket`` per bones-layer ticket id with the
v2 extension fields populated from the epic context, leaves existing
tickets untouched (idempotency), and returns an empty list when the
plan is missing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jig.coordinator import (
    BONES_DEFAULT_DEV_TIER,
    DEFAULT_AUTHOR,
    Coordinator,
)
from jig.intent import Intent
from jig.schemas.plan import (
    BuildPlan,
    Epic,
    EpicLayers,
    LayerStatus,
)
from jig.spec_loader import write_build_plan
from jig.store.tickets import TicketStore
from jig.ticket import Ticket, TicketStatus, WorkType
from tests._test_ticket import TICKET_AC_PLACEHOLDER, EPIC_AC_PLACEHOLDER_BULLET


# ---- fixtures ------------------------------------------------------------


def _intent(problem: str = "Validate spine end-to-end") -> Intent:
    return Intent(problem=problem, simplest_solution="Single ticket")


def _plan(
    *,
    project: str = "bones-demo",
    epic_id: str = "catalog-ingest",
    epic_title: str = "Catalog ingest",
    suite: str = "catalog",
    modules: list[str] | None = None,
    bones_tickets: list[str] | None = None,
    risks: list[str] | None = None,
    acceptance_criteria: list[str] | None = None,
) -> BuildPlan:
    return BuildPlan(
        project=project,
        epics=[
            Epic(
                id=epic_id,
                title=epic_title,
                suite=suite,
                modules=modules if modules is not None else ["catalog-ingest"],
                layers=EpicLayers(
                    bones=LayerStatus(
                        tickets=bones_tickets
                        if bones_tickets is not None
                        else ["tb-catalog-ingest"]
                    ),
                ),
                risks_addressed=risks if risks is not None else [],
                intent=_intent(),
                acceptance_criteria=(
                    acceptance_criteria
                    if acceptance_criteria is not None
                    else [EPIC_AC_PLACEHOLDER_BULLET]
                ),
            )
        ],
    )


@pytest.fixture
async def store(tmp_path: Path) -> TicketStore:
    s = TicketStore(tmp_path / "tickets.jsonl")
    await s.load()
    return s


# ---- happy path ----------------------------------------------------------


@pytest.mark.asyncio
async def test_materialize_creates_bones_ticket(tmp_path: Path, store: TicketStore):
    write_build_plan(tmp_path, _plan())
    coord = Coordinator(tickets=store, project_root=tmp_path)

    created = await coord.materialize_ready_tickets()

    assert created == ["tb-catalog-ingest"]
    t = await store.get("tb-catalog-ingest")
    assert t is not None
    assert t.status == TicketStatus.OPEN  # ready for orchestrator dispatch
    assert t.work_type == WorkType.FEATURE
    assert t.created_by == DEFAULT_AUTHOR


@pytest.mark.asyncio
async def test_materialize_populates_v2_extension_fields(
    tmp_path: Path, store: TicketStore
):
    """Per ``docs/v2.0/pm-workflow/design.md`` §"Ticket structure (extensions)"."""
    write_build_plan(
        tmp_path,
        _plan(
            modules=["catalog-ingest"],
            risks=["r-shopify-delta"],
        ),
    )
    coord = Coordinator(tickets=store, project_root=tmp_path)
    await coord.materialize_ready_tickets()

    t = await store.get("tb-catalog-ingest")
    assert t is not None
    assert t.epic_id == "catalog-ingest"
    assert t.suite_id == "catalog"
    assert t.module_id == "catalog-ingest"
    assert t.layer == "bones"
    assert t.dev_tier == BONES_DEFAULT_DEV_TIER
    assert t.risks_addressed == ["r-shopify-delta"]


@pytest.mark.asyncio
async def test_materialize_handles_module_less_epic(tmp_path: Path, store: TicketStore):
    """epic.modules can be empty during early planning — module_id stays None."""
    write_build_plan(tmp_path, _plan(modules=[]))
    coord = Coordinator(tickets=store, project_root=tmp_path)
    await coord.materialize_ready_tickets()

    t = await store.get("tb-catalog-ingest")
    assert t is not None
    assert t.module_id is None


@pytest.mark.asyncio
async def test_materialize_renders_epic_acceptance_criteria_into_ticket(
    tmp_path: Path, store: TicketStore
) -> None:
    """End-to-end wiring: ``Epic.acceptance_criteria`` bullets reach the
    materialized Ticket's description verbatim under a ``## Acceptance
    criteria`` heading.

    Without this test, a future refactor that misrouted the field
    (e.g. passing ``epic.intent.problem`` to the renderer instead of
    ``epic.acceptance_criteria``) would not be caught by the existing
    unit-only coverage of ``_render_description_with_ac``."""
    bullets = [
        "Row visible in products collection within 30s of OAuth completion.",
        "Subsequent fetches see no duplicate rows.",
    ]
    write_build_plan(tmp_path, _plan(acceptance_criteria=bullets))
    coord = Coordinator(tickets=store, project_root=tmp_path)
    await coord.materialize_ready_tickets()

    t = await store.get("tb-catalog-ingest")
    assert t is not None
    assert "## Acceptance criteria" in t.description
    for bullet in bullets:
        assert f"- {bullet}" in t.description


# ---- idempotency ---------------------------------------------------------


@pytest.mark.asyncio
async def test_materialize_idempotent(tmp_path: Path, store: TicketStore):
    """Re-running over an unchanged plan is a no-op."""
    write_build_plan(tmp_path, _plan())
    coord = Coordinator(tickets=store, project_root=tmp_path)

    first = await coord.materialize_ready_tickets()
    second = await coord.materialize_ready_tickets()

    assert first == ["tb-catalog-ingest"]
    assert second == []


@pytest.mark.asyncio
async def test_materialize_skips_pre_existing_ticket(
    tmp_path: Path, store: TicketStore
):
    """A Planner-authored ticket already in the store is left alone.

    Track F MVP will have the Planner write tickets directly via its
    own MCP tools; the Coordinator must not overwrite those.
    """
    pre_existing = Ticket(
        id="tb-catalog-ingest",
        work_type=WorkType.FEATURE,
        title="Pre-authored",
        description="Authored by the Planner" + "\n" + TICKET_AC_PLACEHOLDER,
        created_by="planner-v2",
    )
    await store.create(pre_existing)

    write_build_plan(tmp_path, _plan())
    coord = Coordinator(tickets=store, project_root=tmp_path)

    created = await coord.materialize_ready_tickets()

    assert created == []
    t = await store.get("tb-catalog-ingest")
    assert t is not None
    assert t.created_by == "planner-v2"
    assert t.title == "Pre-authored"


# ---- absence handling ----------------------------------------------------


@pytest.mark.asyncio
async def test_materialize_returns_empty_when_plan_missing(
    tmp_path: Path, store: TicketStore
):
    """No build plan == nothing to do; bones doesn't error."""
    coord = Coordinator(tickets=store, project_root=tmp_path)
    assert await coord.materialize_ready_tickets() == []


@pytest.mark.asyncio
async def test_materialize_handles_empty_bones_layer(
    tmp_path: Path, store: TicketStore
):
    """An epic with no bones tickets contributes nothing."""
    write_build_plan(tmp_path, _plan(bones_tickets=[]))
    coord = Coordinator(tickets=store, project_root=tmp_path)
    assert await coord.materialize_ready_tickets() == []
    assert await store.list_all() == []


# ---- multi-epic / scope --------------------------------------------------


@pytest.mark.asyncio
async def test_materialize_walks_multiple_epics(tmp_path: Path, store: TicketStore):
    """Bones-scope ships one epic, but the loop over plan.epics must not
    silently cap at the first — Track F MVP scenarios will hit this."""
    plan = BuildPlan(
        project="multi-epic",
        epics=[
            Epic(
                id="catalog-ingest",
                title="Catalog ingest",
                suite="catalog",
                modules=["catalog-ingest"],
                layers=EpicLayers(bones=LayerStatus(tickets=["tb-catalog"])),
                intent=_intent(),
                acceptance_criteria=[EPIC_AC_PLACEHOLDER_BULLET],
            ),
            Epic(
                id="categorization",
                title="Categorization",
                suite="catalog",
                modules=["categorization"],
                layers=EpicLayers(bones=LayerStatus(tickets=["tb-categorize"])),
                intent=_intent("Bucket products"),
                acceptance_criteria=[EPIC_AC_PLACEHOLDER_BULLET],
            ),
        ],
    )
    write_build_plan(tmp_path, plan)
    coord = Coordinator(tickets=store, project_root=tmp_path)

    created = await coord.materialize_ready_tickets()

    assert sorted(created) == ["tb-catalog", "tb-categorize"]
    cat = await store.get("tb-categorize")
    assert cat is not None
    assert cat.epic_id == "categorization"
    assert cat.module_id == "categorization"


@pytest.mark.asyncio
async def test_materialize_ignores_mvp_and_final_layers(
    tmp_path: Path, store: TicketStore
):
    """Bones-scope ships bones-layer dispatch only.

    Layer promotion (all-bones-done before any-MVP) lands in F5/F10;
    bones must not accidentally pre-create MVP tickets and let the
    orchestrator dispatch them out of order.
    """
    from jig.schemas.plan import EpicLayers, LayerStatus

    plan = BuildPlan(
        project="layered",
        epics=[
            Epic(
                id="catalog-ingest",
                title="Catalog ingest",
                suite="catalog",
                modules=["catalog-ingest"],
                layers=EpicLayers(
                    bones=LayerStatus(tickets=["tb-catalog"]),
                    mvp=LayerStatus(tickets=["t-shopify-oauth", "t-csv"]),
                    final=LayerStatus(tickets=["t-rate-limit"]),
                ),
                intent=_intent(),
                acceptance_criteria=[EPIC_AC_PLACEHOLDER_BULLET],
            )
        ],
    )
    write_build_plan(tmp_path, plan)
    coord = Coordinator(tickets=store, project_root=tmp_path)

    created = await coord.materialize_ready_tickets()

    assert created == ["tb-catalog"]
    assert await store.get("t-shopify-oauth") is None
    assert await store.get("t-csv") is None
    assert await store.get("t-rate-limit") is None


# ---- ready for dispatch --------------------------------------------------


@pytest.mark.asyncio
async def test_materialized_tickets_are_findable_as_ready(
    tmp_path: Path, store: TicketStore
):
    """The whole point of materialize is to feed orchestrator dispatch.

    ``find_ready`` is what ``Orchestrator._start_ready_tickets`` calls;
    bones must produce tickets that come out of that query so the
    existing pipeline picks them up without extra wiring.
    """
    write_build_plan(tmp_path, _plan())
    coord = Coordinator(tickets=store, project_root=tmp_path)
    await coord.materialize_ready_tickets()

    ready = await store.find_ready()
    assert [t.id for t in ready] == ["tb-catalog-ingest"]
