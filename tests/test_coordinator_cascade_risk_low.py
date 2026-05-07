"""Coordinator bones-first override via ``cascade_risk_low`` (Track C Final).

Per ``docs/v2.0/pm-workflow/design.md`` §"Bones-first ordering":
- Strict bones-first is the default.
- Per-epic operator override (``/plan unblock``) is operator-driven
  and lives outside the Coordinator.
- ``cascade_risk_low`` flag from SA is the system-suggested override:
  when every still-blocked bones epic touches modules SA flagged
  ``cascade_risk_low=true``, Coordinator allows MVP promotion on
  the unblocked epics.

This module is the regression suite for the third path.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jig.coordinator import Coordinator
from jig.intent import ComplicationsConsidered, Intent
from jig.sa_incremental_mcp import (
    handle_arch_set_cascade_risk_low,
    handle_arch_set_module,
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


def _intent() -> Intent:
    return Intent(
        problem="seed",
        simplest_solution="seed",
        complications_considered=ComplicationsConsidered(),
    )


def _module_payload(mid: str) -> dict:
    return {
        "id": mid,
        "title": mid,
        "summary": "seed",
        "intent": _intent().model_dump(),
        "n_a_categories": ["behavioral_contracts"],
    }


def _epic(eid: str, modules: list[str], *, bones_done: bool) -> Epic:
    """Single-ticket bones epic with one MVP ticket. Status set per arg."""
    bones_status = LayerStatusEnum.DONE if bones_done else LayerStatusEnum.IN_PROGRESS
    return Epic(
        id=eid,
        suite="suite-1",
        title=eid,
        modules=modules,
        intent=_intent(),
        layers=EpicLayers(
            bones=LayerStatus(
                tickets=[f"{eid}-bones-1"],
                status=bones_status,
            ),
            mvp=LayerStatus(
                tickets=[f"{eid}-mvp-1"],
                status=LayerStatusEnum.NOT_STARTED,
            ),
            final=LayerStatus(tickets=[], status=LayerStatusEnum.NOT_STARTED),
        ),
    )


@pytest.fixture
async def wired(tmp_path: Path):
    spec_dir = tmp_path / ".jig" / "spec"
    spec_dir.mkdir(parents=True)
    plan_dir = tmp_path / ".jig" / "plan"
    plan_dir.mkdir(parents=True)
    tickets = TicketStore(tmp_path / ".jig" / "store" / "tickets.jsonl")
    await tickets.load()
    coord = Coordinator(tickets=tickets, project_root=tmp_path)
    return {
        "tickets": tickets,
        "project_path": tmp_path,
        "coord": coord,
    }


# ---- baseline: no flag → strict bones-first ----------------------------


@pytest.mark.asyncio
async def test_no_flag_strict_bones_first(wired):
    """Without cascade_risk_low, blocked bones holds back MVP per spec."""
    project = wired["project_path"]
    coord: Coordinator = wired["coord"]
    await handle_arch_set_module(
        project_path=project, module=_module_payload("m-a")
    )
    await handle_arch_set_module(
        project_path=project, module=_module_payload("m-b")
    )
    plan = BuildPlan(
        project="test",
        ordering_rule=OrderingRule.BONES_FIRST,
        epics=[
            _epic("epic-1", ["m-a"], bones_done=True),
            _epic("epic-2", ["m-b"], bones_done=False),
        ],
    )
    write_build_plan(project, plan)
    assert coord.next_layer_ready(plan) == "bones"


# ---- with flag → MVP promotion ------------------------------------------


@pytest.mark.asyncio
async def test_flag_on_blocked_module_promotes_mvp(wired):
    """Blocked bones touches only cascade_risk_low → returns MVP."""
    project = wired["project_path"]
    coord: Coordinator = wired["coord"]
    await handle_arch_set_module(
        project_path=project, module=_module_payload("m-a")
    )
    await handle_arch_set_module(
        project_path=project, module=_module_payload("m-b")
    )
    # Flag the BLOCKED epic's module.
    await handle_arch_set_cascade_risk_low(
        project_path=project,
        module_id="m-b",
        low=True,
        rationale="no shared shapes; standalone CRUD",
    )
    plan = BuildPlan(
        project="test",
        ordering_rule=OrderingRule.BONES_FIRST,
        epics=[
            _epic("epic-1", ["m-a"], bones_done=True),
            _epic("epic-2", ["m-b"], bones_done=False),
        ],
    )
    write_build_plan(project, plan)
    assert coord.next_layer_ready(plan) == "mvp"


@pytest.mark.asyncio
async def test_partial_flag_does_not_promote(wired):
    """If some blocked epics' modules lack the flag → strict bones holds."""
    project = wired["project_path"]
    coord: Coordinator = wired["coord"]
    for mid in ("m-a", "m-b", "m-c"):
        await handle_arch_set_module(
            project_path=project, module=_module_payload(mid)
        )
    # Only m-b flagged; m-c stays unflagged on a separate blocked epic.
    await handle_arch_set_cascade_risk_low(
        project_path=project,
        module_id="m-b",
        low=True,
        rationale="standalone",
    )
    plan = BuildPlan(
        project="test",
        ordering_rule=OrderingRule.BONES_FIRST,
        epics=[
            _epic("epic-1", ["m-a"], bones_done=True),
            _epic("epic-2", ["m-b"], bones_done=False),
            _epic("epic-3", ["m-c"], bones_done=False),
        ],
    )
    write_build_plan(project, plan)
    assert coord.next_layer_ready(plan) == "bones"


@pytest.mark.asyncio
async def test_no_blocked_epics_returns_bones_when_no_done_either(wired):
    """All bones in_progress, no done → no promote (override needs at least one done)."""
    project = wired["project_path"]
    coord: Coordinator = wired["coord"]
    await handle_arch_set_module(
        project_path=project, module=_module_payload("m-a")
    )
    await handle_arch_set_cascade_risk_low(
        project_path=project,
        module_id="m-a",
        low=True,
        rationale="standalone",
    )
    plan = BuildPlan(
        project="test",
        ordering_rule=OrderingRule.BONES_FIRST,
        epics=[_epic("epic-1", ["m-a"], bones_done=False)],
    )
    write_build_plan(project, plan)
    assert coord.next_layer_ready(plan) == "bones"


@pytest.mark.asyncio
async def test_per_epic_ordering_unaffected_by_override(wired):
    """PER_EPIC ordering doesn't apply the override (different rule entirely)."""
    project = wired["project_path"]
    coord: Coordinator = wired["coord"]
    await handle_arch_set_module(
        project_path=project, module=_module_payload("m-a")
    )
    await handle_arch_set_module(
        project_path=project, module=_module_payload("m-b")
    )
    await handle_arch_set_cascade_risk_low(
        project_path=project,
        module_id="m-b",
        low=True,
        rationale="standalone",
    )
    plan = BuildPlan(
        project="test",
        ordering_rule=OrderingRule.PER_EPIC,
        epics=[
            _epic("epic-1", ["m-a"], bones_done=True),
            _epic("epic-2", ["m-b"], bones_done=False),
        ],
    )
    write_build_plan(project, plan)
    # PER_EPIC walks epic-1's chain first (bones done → mvp next).
    assert coord.next_layer_ready(plan) == "mvp"


@pytest.mark.asyncio
async def test_arch_yaml_missing_returns_strict_bones(wired):
    """Architecture.yaml absence → fail-closed (strict bones-first)."""
    project = wired["project_path"]
    coord: Coordinator = wired["coord"]
    plan = BuildPlan(
        project="test",
        ordering_rule=OrderingRule.BONES_FIRST,
        epics=[
            _epic("epic-1", ["m-a"], bones_done=True),
            _epic("epic-2", ["m-b"], bones_done=False),
        ],
    )
    write_build_plan(project, plan)
    # No architecture.yaml exists at all → no flag visible → strict bones.
    assert coord.next_layer_ready(plan) == "bones"


@pytest.mark.asyncio
async def test_blocked_epic_with_no_modules_does_not_promote(wired):
    """An epic with empty modules list can't be classified → keep strict."""
    project = wired["project_path"]
    coord: Coordinator = wired["coord"]
    await handle_arch_set_module(
        project_path=project, module=_module_payload("m-a")
    )
    plan = BuildPlan(
        project="test",
        ordering_rule=OrderingRule.BONES_FIRST,
        epics=[
            _epic("epic-1", ["m-a"], bones_done=True),
            _epic("epic-2", [], bones_done=False),  # no module info
        ],
    )
    write_build_plan(project, plan)
    assert coord.next_layer_ready(plan) == "bones"


# ---- arch_set_cascade_risk_low handler --------------------------------


@pytest.mark.asyncio
async def test_arch_set_cascade_risk_low_persists_flag(tmp_path: Path):
    spec_dir = tmp_path / ".jig" / "spec"
    spec_dir.mkdir(parents=True)
    await handle_arch_set_module(
        project_path=tmp_path, module=_module_payload("m-x")
    )
    await handle_arch_set_cascade_risk_low(
        project_path=tmp_path,
        module_id="m-x",
        low=True,
        rationale="no shared shapes",
    )
    from jig.spec_loader import load_architecture

    arch = load_architecture(tmp_path)
    m = next(m for m in arch.modules if m.id == "m-x")
    assert m.cascade_risk_low is True
    assert m.cascade_risk_low_rationale == "no shared shapes"


@pytest.mark.asyncio
async def test_arch_set_cascade_risk_low_unknown_module_raises(tmp_path: Path):
    spec_dir = tmp_path / ".jig" / "spec"
    spec_dir.mkdir(parents=True)
    with pytest.raises(KeyError):
        await handle_arch_set_cascade_risk_low(
            project_path=tmp_path,
            module_id="m-bogus",
            low=True,
            rationale="..",
        )


@pytest.mark.asyncio
async def test_arch_set_cascade_risk_low_requires_rationale_when_true(tmp_path: Path):
    spec_dir = tmp_path / ".jig" / "spec"
    spec_dir.mkdir(parents=True)
    await handle_arch_set_module(
        project_path=tmp_path, module=_module_payload("m-x")
    )
    with pytest.raises(ValueError):
        await handle_arch_set_cascade_risk_low(
            project_path=tmp_path,
            module_id="m-x",
            low=True,
            rationale="",
        )


@pytest.mark.asyncio
async def test_arch_set_cascade_risk_low_false_clears_rationale(tmp_path: Path):
    spec_dir = tmp_path / ".jig" / "spec"
    spec_dir.mkdir(parents=True)
    await handle_arch_set_module(
        project_path=tmp_path, module=_module_payload("m-x")
    )
    await handle_arch_set_cascade_risk_low(
        project_path=tmp_path,
        module_id="m-x",
        low=True,
        # >= 10 chars so the schema-level invariant (Block A.1)
        # accepts the rationale.
        rationale="seed-rationale-text",
    )
    await handle_arch_set_cascade_risk_low(
        project_path=tmp_path,
        module_id="m-x",
        low=False,
        rationale="not relevant anymore",
    )
    from jig.spec_loader import load_architecture

    arch = load_architecture(tmp_path)
    m = next(m for m in arch.modules if m.id == "m-x")
    assert m.cascade_risk_low is False
    assert m.cascade_risk_low_rationale is None
