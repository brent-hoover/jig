"""TUI-driving mode hook (Track H Final stub).

Per ``docs/synthetic-operator/design.md`` §"Driver options", scenarios
can opt into routing step invocations through a TUI driver adapter.
For Final scope this is a stub — the adapter records intended TUI
interactions to ``.jig/sim/tui-trace.jsonl`` and dispatches to the
same MCP-direct handlers (tagged with ``tui_via``). Real TUI driving
lands with the TUI track Final.

Tests verify:
- the ``tui_mode`` flag flows from constructor to runtime
- the trace log records one row per intended interaction
- existing scripted scenarios still pass when run in tui_mode
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from jig.sim.driver import TUI_TRACE_RELPATH, Driver, TuiDriverAdapter
from jig.sim.scenario import Scenario, ScenarioStep, StepKind, load_scenario


def _seed_repo(root: Path) -> None:
    subprocess.run(
        ["git", "init", "-b", "main"], cwd=root, check=True, capture_output=True
    )
    for k, v in (
        ("user.email", "test@example.com"),
        ("user.name", "Test"),
        ("commit.gpgsign", "false"),
    ):
        subprocess.run(
            ["git", "config", k, v], cwd=root, check=True, capture_output=True
        )
    (root / "README.md").write_text("seed\n")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "seed"], cwd=root, check=True, capture_output=True
    )


# ---- driver flag ---------------------------------------------------------


def test_driver_tui_mode_default_false():
    d = Driver()
    assert d.tui_mode is False


def test_driver_tui_mode_flag():
    d = Driver(tui_mode=True)
    assert d.tui_mode is True


# ---- TuiDriverAdapter standalone ----------------------------------------


async def test_tui_adapter_creates_trace_file(tmp_path: Path):
    adapter = TuiDriverAdapter(project_root=tmp_path)
    await adapter.start()
    assert (tmp_path / TUI_TRACE_RELPATH).is_file()


async def test_tui_adapter_records_jsonl_rows(tmp_path: Path):
    adapter = TuiDriverAdapter(project_root=tmp_path)
    await adapter.start()
    await adapter.record(
        step_kind="materialize_tickets", params={"x": 1}, tag="tui"
    )
    await adapter.record(
        step_kind="run_reviewer", params={"ticket_id": "t1"}, tag="tui"
    )
    text = (tmp_path / TUI_TRACE_RELPATH).read_text()
    rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    assert len(rows) == 2
    assert rows[0]["step_kind"] == "materialize_tickets"
    assert rows[0]["tui_via"] == "tui"
    assert rows[1]["step_kind"] == "run_reviewer"


async def test_tui_adapter_truncates_on_start(tmp_path: Path):
    """``start()`` truncates the trace so each run begins clean."""
    adapter = TuiDriverAdapter(project_root=tmp_path)
    await adapter.start()
    await adapter.record(step_kind="x", params={}, tag="tui")
    # New start should clear it.
    await adapter.start()
    assert (tmp_path / TUI_TRACE_RELPATH).read_text() == ""


# ---- driver integration --------------------------------------------------


@pytest.mark.asyncio
async def test_driver_tui_mode_records_per_step(tmp_path: Path):
    """A scenario run in tui_mode records one trace row per step."""
    _seed_repo(tmp_path)
    scn = Scenario(
        id="tui-x",
        description="x",
        persona="methodical",
        estimated_cost_usd_max=0.0,
        steps=[
            ScenarioStep(kind=StepKind.MATERIALIZE_TICKETS, params={}),
            ScenarioStep(kind=StepKind.MATERIALIZE_TICKETS, params={}),
        ],
        final_assertions=[],
    )
    driver = Driver(tui_mode=True)
    report = await driver.run(scn, project_root=tmp_path)
    assert report.passed, report.failure_summary()

    trace = (tmp_path / TUI_TRACE_RELPATH).read_text()
    rows = [json.loads(line) for line in trace.splitlines() if line.strip()]
    assert len(rows) == 2
    assert all(r["step_kind"] == "materialize_tickets" for r in rows)
    assert all(r["tui_via"] == "tui" for r in rows)


@pytest.mark.asyncio
async def test_driver_tui_mode_keeps_existing_scenario_green(tmp_path: Path):
    """The bones-walking-skeleton scenario still passes when run in tui_mode."""
    _seed_repo(tmp_path)
    scenario_path = (
        Path(__file__).parent / "scenarios" / "bones-walking-skeleton.scenario.yaml"
    )
    scn = load_scenario(scenario_path)
    driver = Driver(tui_mode=True)
    report = await driver.run(scn, project_root=tmp_path)
    assert report.passed, report.failure_summary()

    # Trace recorded one row per scripted step.
    trace = (tmp_path / TUI_TRACE_RELPATH).read_text()
    rows = [json.loads(line) for line in trace.splitlines() if line.strip()]
    assert len(rows) == len(scn.steps)


@pytest.mark.asyncio
async def test_driver_non_tui_mode_writes_no_trace(tmp_path: Path):
    """tui_mode=False (default) leaves no trace file."""
    _seed_repo(tmp_path)
    scn = Scenario(
        id="no-tui",
        description="x",
        persona="methodical",
        estimated_cost_usd_max=0.0,
        steps=[ScenarioStep(kind=StepKind.MATERIALIZE_TICKETS, params={})],
        final_assertions=[],
    )
    driver = Driver()
    report = await driver.run(scn, project_root=tmp_path)
    assert report.passed
    assert not (tmp_path / TUI_TRACE_RELPATH).is_file()
