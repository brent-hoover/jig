"""Regression-scenario discipline CLI (Track H Final).

Tests ``jig sim regression new`` (scaffold) + ``jig sim regression list``
+ from-scenario cloning + the load-bearing convention that scaffolded
files load cleanly via ``load_scenario``.
"""
from __future__ import annotations

from pathlib import Path

import yaml
from click.testing import CliRunner

from jig.sim.cli import sim
from jig.sim.scenario import load_scenario


def test_regression_new_scaffolds_minimal_scenario(tmp_path: Path):
    """``jig sim regression new`` creates a valid scenario YAML."""
    runner = CliRunner()
    result = runner.invoke(
        sim,
        [
            "regression",
            "new",
            "--bug-id",
            "cascade-overlap",
            "--description",
            "two spikes complete with overlapping cascade proposals",
            "--scenarios",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    out_path = tmp_path / "regressions" / "cascade-overlap.scenario.yaml"
    assert out_path.is_file()

    # Header comments + valid YAML body.
    text = out_path.read_text()
    assert "Regression scenario for bug: cascade-overlap" in text
    assert "two spikes complete" in text

    # Loads cleanly through the scenario loader.
    scn = load_scenario(out_path)
    assert scn.id == "regression-cascade-overlap"
    assert scn.persona == "methodical"
    assert "regression-scenarios" in scn.coverage_tags
    # Empty steps list flags it as a scaffold for `regression list`.
    assert scn.steps == []


def test_regression_new_includes_realism_gap_id(tmp_path: Path):
    runner = CliRunner()
    result = runner.invoke(
        sim,
        [
            "regression",
            "new",
            "--bug-id",
            "orphan-namespace",
            "--description",
            "agent crashes mid-ticket; cleanup didn't fire",
            "--scenarios",
            str(tmp_path),
            "--realism-gap-id",
            "gap-xyz",
        ],
    )
    assert result.exit_code == 0, result.output
    text = (
        tmp_path / "regressions" / "orphan-namespace.scenario.yaml"
    ).read_text()
    assert "Originating realism-gap: gap-xyz" in text


def test_regression_new_persona_overrideable(tmp_path: Path):
    runner = CliRunner()
    result = runner.invoke(
        sim,
        [
            "regression",
            "new",
            "--bug-id",
            "x",
            "--description",
            "x",
            "--persona",
            "hostile",
            "--scenarios",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    scn = load_scenario(tmp_path / "regressions" / "x.scenario.yaml")
    assert scn.persona == "hostile"


def test_regression_new_refuses_overwrite(tmp_path: Path):
    """Re-running with the same bug-id refuses to clobber an existing file."""
    runner = CliRunner()
    args = [
        "regression",
        "new",
        "--bug-id",
        "x",
        "--description",
        "x",
        "--scenarios",
        str(tmp_path),
    ]
    runner.invoke(sim, args)
    result = runner.invoke(sim, args)
    assert result.exit_code == 1, result.output
    assert "refused to overwrite" in result.output


def test_regression_new_from_scenario_clones_structure(tmp_path: Path):
    """``--from-scenario`` clones an existing scenario's shape."""
    runner = CliRunner()
    # Use one of the in-tree scenarios as the source.
    src_scenario = (
        Path(__file__).parent
        / "scenarios"
        / "bones-walking-skeleton.scenario.yaml"
    )
    assert src_scenario.is_file()
    result = runner.invoke(
        sim,
        [
            "regression",
            "new",
            "--bug-id",
            "bones-doesnt-converge",
            "--description",
            "SA delta-amends fail twice; should escalate",
            "--scenarios",
            str(tmp_path),
            "--from-scenario",
            str(src_scenario),
        ],
    )
    assert result.exit_code == 0, result.output
    scn = load_scenario(
        tmp_path / "regressions" / "bones-doesnt-converge.scenario.yaml"
    )
    # Cloned shape: steps came from the source scenario.
    assert scn.steps  # non-empty (the clone worked)
    # ID + description got patched.
    assert scn.id == "regression-bones-doesnt-converge"
    assert "delta-amends" in scn.description
    # Regression marker tag got appended.
    assert "regression-scenarios" in scn.coverage_tags


def test_regression_list_empty(tmp_path: Path):
    runner = CliRunner()
    result = runner.invoke(
        sim, ["regression", "list", "--scenarios", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    assert "no regressions" in result.output.lower() or "no regression" in result.output.lower()


def test_regression_list_reports_scaffold_and_ready_status(tmp_path: Path):
    """List shows bug id + status + description for each scenario."""
    runner = CliRunner()
    # Scaffold a fresh one (status=scaffold, no steps).
    runner.invoke(
        sim,
        [
            "regression",
            "new",
            "--bug-id",
            "alpha",
            "--description",
            "alpha bug",
            "--scenarios",
            str(tmp_path),
        ],
    )
    # Hand-author one with steps (status=ready).
    out = tmp_path / "regressions" / "beta.scenario.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        yaml.safe_dump(
            {
                "spec_version": 1,
                "id": "regression-beta",
                "description": "beta bug — fully reproduces",
                "persona": "methodical",
                "estimated_cost_usd_max": 0.0,
                "tier": "smoke",
                "coverage_tags": ["regression-scenarios"],
                "steps": [{"kind": "materialize_tickets"}],
                "final_assertions": [],
            }
        )
    )

    result = runner.invoke(
        sim, ["regression", "list", "--scenarios", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    assert "alpha" in result.output
    assert "[scaffold]" in result.output
    assert "beta" in result.output
    assert "[ready]" in result.output
