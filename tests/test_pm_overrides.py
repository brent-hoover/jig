"""Bones-first override audit (Track F Final) — store + emit + CLI."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from jig.analytics.emitter import EventEmitter
from jig.analytics.events import BonesPromotedIncomplete
from jig.analytics.store import AnalyticsStore
from jig.cli import cli
from jig.coordinator import Coordinator
from jig.pm.overrides import (
    OVERRIDE_RELPATH,
    OverrideEntry,
    OverrideStore,
    list_overrides,
    record_unblock_override,
)


# ---- store --------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_round_trip(tmp_path: Path):
    store = OverrideStore(tmp_path)
    await store.load()
    assert store.all() == []
    await store.append(
        OverrideEntry(
            epic_id="epic-1",
            rationale="cascade risk low",
            cascade_risk_low_acknowledged=True,
            still_running_bones_epic_ids=["epic-2"],
            sa_marked_cascade_risk_low_epic_ids=["epic-2"],
        )
    )
    assert store.path == tmp_path / OVERRIDE_RELPATH
    store2 = OverrideStore(tmp_path)
    await store2.load()
    assert len(store2.all()) == 1
    assert store2.all()[0].epic_id == "epic-1"


@pytest.mark.asyncio
async def test_record_unblock_override_emits_event(tmp_path: Path):
    analytics = AnalyticsStore(tmp_path / "analytics.jsonl")
    await analytics.load()
    emitter = EventEmitter(analytics)

    entry = await record_unblock_override(
        project_root=tmp_path,
        epic_id="epic-1",
        rationale="cascade risk low",
        cascade_risk_low_acknowledged=True,
        still_running_bones_epic_ids=["epic-2"],
        sa_marked_cascade_risk_low_epic_ids=["epic-2"],
        emitter=emitter,
    )
    await emitter.drain()
    assert entry.epic_id == "epic-1"

    events = await analytics.by_kind("bones_promoted_incomplete")
    assert len(events) == 1
    ev = events[0]
    assert isinstance(ev, BonesPromotedIncomplete)
    assert ev.promoted_epic_ids == ["epic-1"]
    assert ev.still_running_bones_epic_ids == ["epic-2"]
    assert ev.sa_marked_cascade_risk_low == ["epic-2"]


def test_list_overrides_returns_chronological_order(tmp_path: Path):
    """list_overrides reads JSONL in file order (chronological by append)."""
    import asyncio as _aio

    async def seed():
        store = OverrideStore(tmp_path)
        await store.load()
        await store.append(OverrideEntry(epic_id="e1"))
        await store.append(OverrideEntry(epic_id="e2"))

    _aio.run(seed())
    rows = list_overrides(tmp_path)
    assert [r.epic_id for r in rows] == ["e1", "e2"]


def test_list_overrides_missing_file_returns_empty(tmp_path: Path):
    assert list_overrides(tmp_path) == []


# ---- Coordinator cascade_risk_low → BonesPromotedIncomplete -------------


@pytest.mark.asyncio
async def test_coordinator_emits_event_on_cascade_override(tmp_path: Path):
    """Two-epic plan: epic-1 bones done, epic-2 in-progress + cascade_risk_low.

    Coordinator's dispatch_cycle promotes MVP via the override; we
    expect ``BonesPromotedIncomplete`` to fire with the proper epic
    breakdown.
    """
    from jig.atomic import atomic_write_text
    from jig.schemas.arch import (
        Architecture,
        Module,
    )
    from jig.intent import Intent
    from jig.schemas.plan import (
        BuildPlan,
        Epic,
        EpicLayers,
        LayerStatus,
        LayerStatusEnum,
        OrderingRule,
    )
    from jig.spec_loader import architecture_path, write_build_plan
    from jig.store.tickets import TicketStore
    from jig.ticket import Size, Ticket, TicketStatus, WorkType

    # Two-module architecture with module-b flagged cascade_risk_low.
    arch = Architecture(
        spec_version=1,
        modules=[
            Module(
                id="module-a",
                title="A",
                summary="a",
                implements_capabilities=["c1"],
                tier_hint="standard",
                requires_tracer_bullet=True,
                intent=Intent(
                    problem="x", simplest_solution="y",
                    complications_considered={
                        "scale": None, "concurrency": None,
                        "failure_modes": None, "cross_cutting": None,
                    },
                ),
            ),
            Module(
                id="module-b",
                title="B",
                summary="b",
                implements_capabilities=["c2"],
                cascade_risk_low=True,
                cascade_risk_low_rationale="standalone",
                tier_hint="standard",
                requires_tracer_bullet=True,
                intent=Intent(
                    problem="x", simplest_solution="y",
                    complications_considered={
                        "scale": None, "concurrency": None,
                        "failure_modes": None, "cross_cutting": None,
                    },
                ),
            ),
        ],
    )
    architecture_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        architecture_path(tmp_path),
        yaml.safe_dump(arch.model_dump(mode="json"), sort_keys=False),
    )

    plan = BuildPlan(
        project="p",
        ordering_rule=OrderingRule.BONES_FIRST,
        epics=[
            Epic(
                id="epic-1",
                title="A epic",
                suite="suite-a",
                modules=["module-a"],
                layers=EpicLayers(
                    bones=LayerStatus(
                        status=LayerStatusEnum.DONE, tickets=["t-1"],
                    ),
                    mvp=LayerStatus(tickets=["t-1-mvp"]),
                ),
                intent=Intent(
                    problem="x", simplest_solution="y",
                    complications_considered={
                        "scale": None, "concurrency": None,
                        "failure_modes": None, "cross_cutting": None,
                    },
                ),
            ),
            Epic(
                id="epic-2",
                title="B epic",
                suite="suite-b",
                modules=["module-b"],
                layers=EpicLayers(
                    bones=LayerStatus(
                        status=LayerStatusEnum.IN_PROGRESS,
                        tickets=["t-2"],
                    ),
                    mvp=LayerStatus(tickets=["t-2-mvp"]),
                ),
                intent=Intent(
                    problem="x", simplest_solution="y",
                    complications_considered={
                        "scale": None, "concurrency": None,
                        "failure_modes": None, "cross_cutting": None,
                    },
                ),
            ),
        ],
    )
    write_build_plan(tmp_path, plan)

    store_dir = tmp_path / ".jig" / "store"
    store_dir.mkdir(parents=True, exist_ok=True)
    tickets = TicketStore(store_dir / "tickets.jsonl")
    await tickets.load()
    # Pre-create the bones tickets so layer status reads consistently.
    await tickets.create(Ticket(
        id="t-1", work_type=WorkType.FEATURE, size=Size.M, title="t1",
        created_by="t", status=TicketStatus.RESOLVED,
    ))
    await tickets.create(Ticket(
        id="t-2", work_type=WorkType.FEATURE, size=Size.M, title="t2",
        created_by="t", status=TicketStatus.IN_PROGRESS,
    ))

    analytics = AnalyticsStore(store_dir / "analytics.jsonl")
    await analytics.load()
    emitter = EventEmitter(analytics)

    coord = Coordinator(
        tickets=tickets, project_root=tmp_path, emitter=emitter,
    )
    result = await coord.dispatch_cycle(tmp_path)
    await emitter.drain()
    # Coordinator promoted MVP via the override.
    assert result.next_layer == "mvp"

    events = await analytics.by_kind("bones_promoted_incomplete")
    assert len(events) == 1
    ev = events[0]
    assert isinstance(ev, BonesPromotedIncomplete)
    assert ev.promoted_epic_ids == ["epic-1"]
    assert ev.still_running_bones_epic_ids == ["epic-2"]
    assert ev.sa_marked_cascade_risk_low == ["epic-2"]


# ---- CLI ---------------------------------------------------------------


def test_cli_pm_plan_unblock_records_override(tmp_path: Path):
    """``jig pm plan unblock <epic-id>`` writes audit + emits event."""
    # Seed a minimal build plan.
    from jig.intent import Intent
    from jig.schemas.plan import (
        BuildPlan,
        Epic,
        EpicLayers,
        LayerStatus,
        LayerStatusEnum,
        OrderingRule,
    )
    from jig.spec_loader import write_build_plan

    plan = BuildPlan(
        project="p",
        ordering_rule=OrderingRule.BONES_FIRST,
        epics=[
            Epic(
                id="epic-1",
                title="x",
                suite="suite-a",
                modules=[],
                layers=EpicLayers(
                    bones=LayerStatus(
                        status=LayerStatusEnum.DONE, tickets=["t1"],
                    ),
                ),
                intent=Intent(
                    problem="x", simplest_solution="y",
                    complications_considered={
                        "scale": None, "concurrency": None,
                        "failure_modes": None, "cross_cutting": None,
                    },
                ),
            ),
            Epic(
                id="epic-2",
                title="y",
                suite="suite-b",
                modules=[],
                layers=EpicLayers(
                    bones=LayerStatus(
                        status=LayerStatusEnum.IN_PROGRESS, tickets=["t2"],
                    ),
                ),
                intent=Intent(
                    problem="x", simplest_solution="y",
                    complications_considered={
                        "scale": None, "concurrency": None,
                        "failure_modes": None, "cross_cutting": None,
                    },
                ),
            ),
        ],
    )
    write_build_plan(tmp_path, plan)
    # Empty store dir → CLI skips analytics emit (still records audit).
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "pm", "plan", "unblock", "epic-1",
            "--rationale", "manual",
            "--path", str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    # Audit landed.
    rows = list_overrides(tmp_path)
    assert len(rows) == 1
    assert rows[0].epic_id == "epic-1"
    assert rows[0].rationale == "manual"


def test_cli_pm_overrides_list_runs(tmp_path: Path):
    runner = CliRunner()
    # Empty list returns helpful message.
    result = runner.invoke(
        cli, ["pm", "overrides", "list", "--path", str(tmp_path)],
    )
    assert result.exit_code == 0
    assert "no overrides" in result.output


def test_cli_pm_plan_unblock_missing_plan_errors(tmp_path: Path):
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["pm", "plan", "unblock", "epic-x", "--path", str(tmp_path)],
    )
    assert result.exit_code != 0
    assert "no build plan" in result.output
