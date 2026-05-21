"""Cycle view data model + markdown rendering (Track F Final, deliverable 4)."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from jig.cli import cli
from jig.coordinator import Coordinator
from jig.intent import Intent
from jig.pm.cycle_view import (
    CoordinatorState,
    CycleViewModel,
    EpicViewModel,
    LayerProgress,
    build_cycle_view,
    format_cycle_view,
)
from jig.schemas.plan import (
    BuildPlan,
    Epic,
    EpicLayers,
    LayerStatus,
    LayerStatusEnum,
    OrderingRule,
)
from jig.spec_loader import write_build_plan
from jig.store.tickets import TicketStore
from jig.ticket import Size, Ticket, TicketStatus, WorkType
from tests._test_ticket import TICKET_AC_PLACEHOLDER


def _intent() -> Intent:
    return Intent(
        problem="x",
        simplest_solution="y",
        complications_considered={
            "scale": None,
            "concurrency": None,
            "failure_modes": None,
            "cross_cutting": None,
        },
    )


def _make_plan(*epic_specs) -> BuildPlan:
    """Build a plan with the supplied epic configurations."""
    epics = []
    for spec in epic_specs:
        bones_status = spec.get("bones_status", LayerStatusEnum.NOT_STARTED)
        bones_tickets = spec.get("bones_tickets", [])
        mvp_status = spec.get("mvp_status", LayerStatusEnum.NOT_STARTED)
        mvp_tickets = spec.get("mvp_tickets", [])
        epics.append(
            Epic(
                id=spec["id"],
                title=spec.get("title", spec["id"]),
                suite=spec.get("suite", "suite-x"),
                modules=spec.get("modules", []),
                layers=EpicLayers(
                    bones=LayerStatus(status=bones_status, tickets=bones_tickets),
                    mvp=LayerStatus(status=mvp_status, tickets=mvp_tickets),
                ),
                intent=_intent(),
                acceptance_criteria=[
                    "Test fixture placeholder; replace if the test cares about AC content."
                ],
            )
        )
    return BuildPlan(
        project="p",
        ordering_rule=OrderingRule.BONES_FIRST,
        epics=epics,
    )


# ---- model construction --------------------------------------------------


def test_models_round_trip_through_pydantic():
    """All cycle-view model classes round-trip cleanly."""
    view = CycleViewModel(
        cycle_revision=2,
        epics=[
            EpicViewModel(
                id="e1",
                title="Epic 1",
                layer_progress={
                    "bones": LayerProgress(
                        status="done",
                        tickets_total=3,
                        tickets_resolved=3,
                    ),
                },
            ),
        ],
        coordinator_state=CoordinatorState(
            next_layer_ready="mvp",
            ordering_rule="bones_first",
        ),
    )
    raw = view.model_dump(mode="json")
    rebuilt = CycleViewModel.model_validate(raw)
    assert rebuilt == view


# ---- build_cycle_view ---------------------------------------------------


@pytest.mark.asyncio
async def test_build_cycle_view_empty_plan_returns_empty_view(tmp_path: Path):
    tickets = TicketStore(tmp_path / "tickets.jsonl")
    await tickets.load()
    coord = Coordinator(tickets=tickets, project_root=tmp_path)
    view = await build_cycle_view(coord, tmp_path)
    assert view.epics == []
    assert view.coordinator_state.next_layer_ready is None


@pytest.mark.asyncio
async def test_build_cycle_view_counts_resolved_and_in_progress(tmp_path: Path):
    plan = _make_plan(
        {
            "id": "epic-1",
            "title": "first",
            "bones_status": LayerStatusEnum.IN_PROGRESS,
            "bones_tickets": ["t-1", "t-2", "t-3"],
        },
    )
    write_build_plan(tmp_path, plan)

    tickets = TicketStore(tmp_path / "tickets.jsonl")
    await tickets.load()
    await tickets.create(
        Ticket(
            id="t-1",
            work_type=WorkType.FEATURE,
            size=Size.M,
            title="t",
            created_by="u",
            status=TicketStatus.RESOLVED,
            description=TICKET_AC_PLACEHOLDER,
        )
    )
    await tickets.create(
        Ticket(
            id="t-2",
            work_type=WorkType.FEATURE,
            size=Size.M,
            title="t",
            created_by="u",
            status=TicketStatus.IN_PROGRESS,
            description=TICKET_AC_PLACEHOLDER,
        )
    )
    await tickets.create(
        Ticket(
            id="t-3",
            work_type=WorkType.FEATURE,
            size=Size.M,
            title="t",
            created_by="u",
            status=TicketStatus.OPEN,
            description=TICKET_AC_PLACEHOLDER,
        )
    )
    coord = Coordinator(tickets=tickets, project_root=tmp_path)
    view = await build_cycle_view(coord, tmp_path)
    assert len(view.epics) == 1
    bones = view.epics[0].layer_progress["bones"]
    assert bones.tickets_total == 3
    assert bones.tickets_resolved == 1
    assert bones.tickets_in_progress == 1
    assert bones.status == "in_progress"


@pytest.mark.asyncio
async def test_build_cycle_view_holding_for_cascades(tmp_path: Path):
    plan = _make_plan(
        {
            "id": "epic-1",
            "bones_status": LayerStatusEnum.BLOCKED,
            "bones_tickets": ["t-1"],
        },
    )
    write_build_plan(tmp_path, plan)
    tickets = TicketStore(tmp_path / "tickets.jsonl")
    await tickets.load()
    coord = Coordinator(tickets=tickets, project_root=tmp_path)
    view = await build_cycle_view(coord, tmp_path)
    assert view.coordinator_state.holding_for_cascades == ["epic-1"]


# ---- format_cycle_view --------------------------------------------------


def test_format_cycle_view_includes_section_headers():
    view = CycleViewModel(
        cycle_revision=1,
        epics=[
            EpicViewModel(
                id="e1",
                title="Epic 1",
                layer_progress={
                    "bones": LayerProgress(
                        status="done",
                        tickets_total=2,
                        tickets_resolved=2,
                    ),
                    "mvp": LayerProgress(status="not_started"),
                    "final": LayerProgress(status="not_started"),
                },
            ),
        ],
        coordinator_state=CoordinatorState(
            next_layer_ready="mvp",
            ordering_rule="bones_first",
        ),
    )
    rendered = format_cycle_view(view)
    assert "# Cycle view" in rendered
    assert "## Coordinator state" in rendered
    assert "## Epics" in rendered
    assert "## Pending auto-escalations" in rendered
    assert "## Recent tier promotions" in rendered
    assert "## Calibration envelopes" in rendered
    assert "e1" in rendered


def test_format_cycle_view_no_epics_renders_placeholder():
    view = CycleViewModel(cycle_revision=1)
    rendered = format_cycle_view(view)
    assert "_(no epics in build plan)_" in rendered


# ---- CLI ---------------------------------------------------------------


def test_pm_view_cli_runs(tmp_path: Path):
    """jig pm view prints the rendered cycle view."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["pm", "view", "--path", str(tmp_path)],
    )
    assert result.exit_code == 0
    assert "# Cycle view" in result.output


def test_pm_view_cli_with_plan(tmp_path: Path):
    """jig pm view shows the epics from a real plan."""
    plan = _make_plan(
        {"id": "epic-x", "title": "x", "bones_tickets": ["t-1"]},
    )
    write_build_plan(tmp_path, plan)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["pm", "view", "--path", str(tmp_path)],
    )
    assert result.exit_code == 0
    assert "epic-x" in result.output
