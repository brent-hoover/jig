"""Coordinator multi-layer dispatch + cycle-aware behavior (Track F MVP).

Extends the bones ``materialize_ready_tickets`` with cycle-aware methods:

- ``materialize_layer(plan, layer_name)`` — generalize materialization to
  any of bones / mvp / final.
- ``advance_layer_status(plan_path)`` — recompute every epic-layer's
  status from the live ticket store; persist if anything changed.
- ``next_layer_ready(plan)`` — honor ``OrderingRule`` (BONES_FIRST /
  PER_EPIC) to surface the next layer the Coordinator should
  materialize, or ``None`` when everything is done.
- ``dispatch_cycle(plan_path)`` — one cycle: refresh statuses,
  materialize the next layer if any, return a ``CycleResult``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jig.coordinator import Coordinator, CycleResult
from jig.intent import Intent
from jig.schemas.plan import (
    BuildPlan,
    Epic,
    EpicLayers,
    LayerStatus,
    LayerStatusEnum,
    OrderingRule,
)
from jig.spec_loader import load_build_plan, write_build_plan
from jig.store.tickets import TicketStore
from jig.ticket import TicketStatus


def _intent(problem: str = "Validate spine") -> Intent:
    return Intent(problem=problem, simplest_solution="Single ticket")


def _epic(
    *,
    epic_id: str = "catalog-ingest",
    suite: str = "catalog",
    modules: list[str] | None = None,
    bones: list[str] | None = None,
    mvp: list[str] | None = None,
    final: list[str] | None = None,
    bones_status: LayerStatusEnum = LayerStatusEnum.NOT_STARTED,
    mvp_status: LayerStatusEnum = LayerStatusEnum.NOT_STARTED,
    final_status: LayerStatusEnum = LayerStatusEnum.NOT_STARTED,
) -> Epic:
    return Epic(
        id=epic_id,
        title=f"{epic_id} title",
        suite=suite,
        modules=modules if modules is not None else ["catalog-ingest"],
        layers=EpicLayers(
            bones=LayerStatus(
                status=bones_status,
                tickets=bones if bones is not None else [],
            ),
            mvp=LayerStatus(
                status=mvp_status,
                tickets=mvp if mvp is not None else [],
            ),
            final=LayerStatus(
                status=final_status,
                tickets=final if final is not None else [],
            ),
        ),
        intent=_intent(),
    )


def _plan(
    *,
    epics: list[Epic] | None = None,
    ordering_rule: OrderingRule = OrderingRule.BONES_FIRST,
) -> BuildPlan:
    return BuildPlan(
        project="cycle-demo",
        ordering_rule=ordering_rule,
        epics=epics
        or [_epic(bones=["tb-cat"], mvp=["t-shopify"], final=["t-rate"])],
    )


@pytest.fixture
async def store(tmp_path: Path) -> TicketStore:
    s = TicketStore(tmp_path / "tickets.jsonl")
    await s.load()
    return s


# ---- materialize_layer ---------------------------------------------------


@pytest.mark.asyncio
async def test_materialize_layer_creates_mvp_tickets(
    tmp_path: Path, store: TicketStore
):
    plan = _plan()
    coord = Coordinator(tickets=store, project_root=tmp_path)

    created = await coord.materialize_layer(plan, "mvp")

    assert created == ["t-shopify"]
    t = await store.get("t-shopify")
    assert t is not None
    assert t.layer == "mvp"
    assert t.epic_id == "catalog-ingest"


@pytest.mark.asyncio
async def test_materialize_layer_skips_existing(
    tmp_path: Path, store: TicketStore
):
    plan = _plan()
    coord = Coordinator(tickets=store, project_root=tmp_path)
    await coord.materialize_layer(plan, "bones")
    second = await coord.materialize_layer(plan, "bones")
    assert second == []


@pytest.mark.asyncio
async def test_materialize_layer_unknown_raises(
    tmp_path: Path, store: TicketStore
):
    coord = Coordinator(tickets=store, project_root=tmp_path)
    with pytest.raises(ValueError):
        await coord.materialize_layer(_plan(), "bogus")


@pytest.mark.asyncio
async def test_materialize_layer_walks_multiple_epics(
    tmp_path: Path, store: TicketStore
):
    plan = _plan(
        epics=[
            _epic(epic_id="e-a", bones=["tb-a"], mvp=["t-a"]),
            _epic(epic_id="e-b", modules=["mod-b"], bones=["tb-b"], mvp=["t-b"]),
        ]
    )
    coord = Coordinator(tickets=store, project_root=tmp_path)
    created = await coord.materialize_layer(plan, "mvp")
    assert sorted(created) == ["t-a", "t-b"]


# ---- advance_layer_status -----------------------------------------------


@pytest.mark.asyncio
async def test_advance_layer_status_marks_done_when_all_resolved(
    tmp_path: Path, store: TicketStore
):
    plan = _plan(
        epics=[_epic(bones=["tb-cat"], bones_status=LayerStatusEnum.IN_PROGRESS)]
    )
    write_build_plan(tmp_path, plan)
    # Materialize then resolve.
    coord = Coordinator(tickets=store, project_root=tmp_path)
    await coord.materialize_layer(plan, "bones")
    await store.update_status("tb-cat", TicketStatus.RESOLVED)

    changed = await coord.advance_layer_status(tmp_path)
    assert changed is True

    refreshed = load_build_plan(tmp_path)
    assert refreshed.epics[0].layers.bones.status == LayerStatusEnum.DONE


@pytest.mark.asyncio
async def test_advance_layer_status_marks_in_progress(
    tmp_path: Path, store: TicketStore
):
    plan = _plan(
        epics=[_epic(bones=["tb-a", "tb-b"])],
    )
    write_build_plan(tmp_path, plan)
    coord = Coordinator(tickets=store, project_root=tmp_path)
    await coord.materialize_layer(plan, "bones")
    await store.update_status("tb-a", TicketStatus.IN_PROGRESS)

    changed = await coord.advance_layer_status(tmp_path)
    assert changed is True

    refreshed = load_build_plan(tmp_path)
    assert (
        refreshed.epics[0].layers.bones.status == LayerStatusEnum.IN_PROGRESS
    )


@pytest.mark.asyncio
async def test_advance_layer_status_partial_resolved_is_in_progress(
    tmp_path: Path, store: TicketStore
):
    plan = _plan(epics=[_epic(bones=["tb-a", "tb-b"])])
    write_build_plan(tmp_path, plan)
    coord = Coordinator(tickets=store, project_root=tmp_path)
    await coord.materialize_layer(plan, "bones")
    await store.update_status("tb-a", TicketStatus.RESOLVED)

    changed = await coord.advance_layer_status(tmp_path)
    assert changed is True

    refreshed = load_build_plan(tmp_path)
    assert (
        refreshed.epics[0].layers.bones.status == LayerStatusEnum.IN_PROGRESS
    )


@pytest.mark.asyncio
async def test_advance_layer_status_marks_blocked_on_failed(
    tmp_path: Path, store: TicketStore
):
    plan = _plan(epics=[_epic(bones=["tb-a", "tb-b"])])
    write_build_plan(tmp_path, plan)
    coord = Coordinator(tickets=store, project_root=tmp_path)
    await coord.materialize_layer(plan, "bones")
    await store.update_status("tb-a", TicketStatus.FAILED)

    changed = await coord.advance_layer_status(tmp_path)
    assert changed is True
    refreshed = load_build_plan(tmp_path)
    assert (
        refreshed.epics[0].layers.bones.status == LayerStatusEnum.BLOCKED
    )


@pytest.mark.asyncio
async def test_advance_layer_status_no_change_returns_false(
    tmp_path: Path, store: TicketStore
):
    plan = _plan(epics=[_epic(bones=["tb-a"])])
    write_build_plan(tmp_path, plan)
    coord = Coordinator(tickets=store, project_root=tmp_path)
    await coord.materialize_layer(plan, "bones")
    # Materialized but not yet started — status stays NOT_STARTED.
    changed = await coord.advance_layer_status(tmp_path)
    assert changed is False


@pytest.mark.asyncio
async def test_advance_layer_status_empty_layer_stays_not_started(
    tmp_path: Path, store: TicketStore
):
    plan = _plan(epics=[_epic(bones=["tb-a"])])  # mvp is empty
    write_build_plan(tmp_path, plan)
    coord = Coordinator(tickets=store, project_root=tmp_path)
    await coord.materialize_layer(plan, "bones")
    await store.update_status("tb-a", TicketStatus.RESOLVED)
    await coord.advance_layer_status(tmp_path)
    refreshed = load_build_plan(tmp_path)
    # Empty mvp layer doesn't get auto-flipped to "done".
    assert refreshed.epics[0].layers.mvp.status == LayerStatusEnum.NOT_STARTED


# ---- next_layer_ready ---------------------------------------------------


def test_next_layer_ready_returns_bones_when_unbuilt():
    plan = _plan(epics=[_epic(bones=["tb-a"], mvp=["t-a"])])
    coord = Coordinator(tickets=None, project_root=Path("/dev/null"))  # type: ignore[arg-type]
    assert coord.next_layer_ready(plan) == "bones"


def test_next_layer_ready_bones_first_waits_until_all_epics_bones_done():
    epics = [
        _epic(epic_id="e-a", bones=["tb-a"], bones_status=LayerStatusEnum.DONE, mvp=["t-a"]),
        _epic(epic_id="e-b", bones=["tb-b"], mvp=["t-b"]),
    ]
    plan = _plan(epics=epics)
    coord = Coordinator(tickets=None, project_root=Path("/dev/null"))  # type: ignore[arg-type]
    # e-b's bones is still NOT_STARTED — bones-first rule keeps us on bones.
    assert coord.next_layer_ready(plan) == "bones"


def test_next_layer_ready_bones_first_returns_mvp_once_all_bones_done():
    epics = [
        _epic(epic_id="e-a", bones=["tb-a"], bones_status=LayerStatusEnum.DONE, mvp=["t-a"]),
        _epic(epic_id="e-b", modules=["mod-b"], bones=["tb-b"], bones_status=LayerStatusEnum.DONE, mvp=["t-b"]),
    ]
    plan = _plan(epics=epics)
    coord = Coordinator(tickets=None, project_root=Path("/dev/null"))  # type: ignore[arg-type]
    assert coord.next_layer_ready(plan) == "mvp"


def test_next_layer_ready_returns_final_after_mvp_all_done():
    epics = [
        _epic(
            epic_id="e-a",
            bones=["tb-a"],
            bones_status=LayerStatusEnum.DONE,
            mvp=["t-a"],
            mvp_status=LayerStatusEnum.DONE,
            final=["f-a"],
        ),
    ]
    plan = _plan(epics=epics)
    coord = Coordinator(tickets=None, project_root=Path("/dev/null"))  # type: ignore[arg-type]
    assert coord.next_layer_ready(plan) == "final"


def test_next_layer_ready_returns_none_when_all_done():
    epics = [
        _epic(
            epic_id="e-a",
            bones=["tb-a"],
            bones_status=LayerStatusEnum.DONE,
            mvp=["t-a"],
            mvp_status=LayerStatusEnum.DONE,
            final=["f-a"],
            final_status=LayerStatusEnum.DONE,
        ),
    ]
    plan = _plan(epics=epics)
    coord = Coordinator(tickets=None, project_root=Path("/dev/null"))  # type: ignore[arg-type]
    assert coord.next_layer_ready(plan) is None


def test_next_layer_ready_per_epic_returns_per_epic_layer():
    """PER_EPIC: each epic walks its own layer chain independently."""
    epics = [
        _epic(epic_id="e-a", bones=["tb-a"], bones_status=LayerStatusEnum.DONE, mvp=["t-a"]),
        _epic(epic_id="e-b", modules=["mod-b"], bones=["tb-b"], mvp=["t-b"]),
    ]
    plan = _plan(epics=epics, ordering_rule=OrderingRule.PER_EPIC)
    coord = Coordinator(tickets=None, project_root=Path("/dev/null"))  # type: ignore[arg-type]
    # e-a is ready for mvp; e-b still on bones — per_epic returns *some*
    # layer-name that's ready (the function gives the next due layer for
    # the first epic ready to advance, biasing earliest-in-list).
    assert coord.next_layer_ready(plan) in {"mvp", "bones"}


def test_next_layer_ready_skips_layer_without_tickets_in_bones_first():
    """An epic with no MVP tickets shouldn't block the rule — it's vacuously done."""
    epics = [
        _epic(
            epic_id="e-a",
            bones=["tb-a"],
            bones_status=LayerStatusEnum.DONE,
            mvp=[],  # vacuously done
            final=["f-a"],
        ),
    ]
    plan = _plan(epics=epics)
    coord = Coordinator(tickets=None, project_root=Path("/dev/null"))  # type: ignore[arg-type]
    # All epics' bones is done AND all mvps are vacuous → final is next.
    assert coord.next_layer_ready(plan) == "final"


# ---- dispatch_cycle ------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_cycle_materializes_bones_first(
    tmp_path: Path, store: TicketStore
):
    plan = _plan(epics=[_epic(bones=["tb-cat"], mvp=["t-shop"])])
    write_build_plan(tmp_path, plan)
    coord = Coordinator(tickets=store, project_root=tmp_path)

    result = await coord.dispatch_cycle(tmp_path)

    assert isinstance(result, CycleResult)
    assert result.tickets_materialized == ["tb-cat"]
    assert result.next_layer == "bones"
    # MVP not materialized yet.
    assert await store.get("t-shop") is None


@pytest.mark.asyncio
async def test_dispatch_cycle_advances_then_materializes_next(
    tmp_path: Path, store: TicketStore
):
    plan = _plan(epics=[_epic(bones=["tb-cat"], mvp=["t-shop"])])
    write_build_plan(tmp_path, plan)
    coord = Coordinator(tickets=store, project_root=tmp_path)

    # Cycle 1: materialize bones.
    result1 = await coord.dispatch_cycle(tmp_path)
    assert result1.tickets_materialized == ["tb-cat"]

    # Resolve the bones ticket.
    await store.update_status("tb-cat", TicketStatus.RESOLVED)

    # Cycle 2: advance bones → done, then materialize mvp.
    result2 = await coord.dispatch_cycle(tmp_path)
    assert result2.tickets_materialized == ["t-shop"]
    assert any(
        epic_id == "catalog-ingest" and layer == "bones" and status == "done"
        for (epic_id, layer, status) in result2.layers_advanced
    )
    assert await store.get("t-shop") is not None


@pytest.mark.asyncio
async def test_dispatch_cycle_returns_none_layer_when_all_done(
    tmp_path: Path, store: TicketStore
):
    plan = _plan(epics=[_epic(bones=["tb-cat"])])
    write_build_plan(tmp_path, plan)
    coord = Coordinator(tickets=store, project_root=tmp_path)

    await coord.dispatch_cycle(tmp_path)
    await store.update_status("tb-cat", TicketStatus.RESOLVED)
    await coord.dispatch_cycle(tmp_path)  # advance + nothing more to materialize

    result = await coord.dispatch_cycle(tmp_path)
    assert result.tickets_materialized == []
    assert result.next_layer is None


@pytest.mark.asyncio
async def test_dispatch_cycle_missing_plan_returns_empty_result(
    tmp_path: Path, store: TicketStore
):
    coord = Coordinator(tickets=store, project_root=tmp_path)
    result = await coord.dispatch_cycle(tmp_path)
    assert result.tickets_materialized == []
    assert result.layers_advanced == []
    assert result.next_layer is None


@pytest.mark.asyncio
async def test_dispatch_cycle_holds_mvp_until_all_epics_bones_done(
    tmp_path: Path, store: TicketStore
):
    """Cross-epic gating per BONES_FIRST."""
    plan = _plan(
        epics=[
            _epic(epic_id="e-a", bones=["tb-a"], mvp=["m-a"]),
            _epic(epic_id="e-b", modules=["mod-b"], bones=["tb-b"], mvp=["m-b"]),
        ]
    )
    write_build_plan(tmp_path, plan)
    coord = Coordinator(tickets=store, project_root=tmp_path)

    await coord.dispatch_cycle(tmp_path)
    await store.update_status("tb-a", TicketStatus.RESOLVED)
    # tb-b still open — MVP must NOT materialize.
    result = await coord.dispatch_cycle(tmp_path)
    assert "m-a" not in result.tickets_materialized
    assert "m-b" not in result.tickets_materialized
    assert result.next_layer == "bones"
