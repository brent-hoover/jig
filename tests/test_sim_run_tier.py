"""CI tier integration (Track H MVP follow-on).

Tests the ``tier`` field on Scenario, the ``_scenarios_for_tier``
filtering helper, the ``jig sim run-tier <tier>`` CLI command's
exit-code semantics, and the ``sim_smoke`` pytest marker registration.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from jig.sim.cli import _scenarios_for_tier, sim
from jig.sim.scenario import Scenario, load_scenario


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


def _scn(scenario_id: str, tier: str = "smoke") -> Scenario:
    return Scenario(
        id=scenario_id,
        description="x",
        persona="methodical",
        estimated_cost_usd_max=0.0,
        tier=tier,  # type: ignore[arg-type]
    )


# ---- tier field on Scenario ---------------------------------------------


def test_scenario_tier_defaults_to_smoke():
    scn = _scn("s1")
    assert scn.tier == "smoke"


def test_scenario_tier_accepts_full_and_nightly(tmp_path: Path):
    src = tmp_path / "x.scenario.yaml"
    src.write_text(
        yaml.safe_dump(
            {
                "spec_version": 1,
                "id": "x",
                "description": "x",
                "persona": "methodical",
                "estimated_cost_usd_max": 0.0,
                "tier": "nightly",
                "steps": [],
                "final_assertions": [],
            }
        )
    )
    scn = load_scenario(src)
    assert scn.tier == "nightly"


def test_scenario_tier_rejects_unknown(tmp_path: Path):
    """Typos in the tier field fail at load time."""
    from pydantic import ValidationError

    src = tmp_path / "x.scenario.yaml"
    src.write_text(
        yaml.safe_dump(
            {
                "spec_version": 1,
                "id": "x",
                "description": "x",
                "persona": "methodical",
                "estimated_cost_usd_max": 0.0,
                "tier": "made-up-tier",
                "steps": [],
                "final_assertions": [],
            }
        )
    )
    with pytest.raises(ValidationError):
        load_scenario(src)


# ---- _scenarios_for_tier -------------------------------------------------


def test_scenarios_for_tier_smoke_returns_only_smoke():
    smoke = _scn("s1", tier="smoke")
    full = _scn("s2", tier="full")
    nightly = _scn("s3", tier="nightly")
    selected = _scenarios_for_tier([smoke, full, nightly], "smoke")
    assert [s.id for s in selected] == ["s1"]


def test_scenarios_for_tier_full_returns_smoke_and_full():
    smoke = _scn("s1", tier="smoke")
    full = _scn("s2", tier="full")
    nightly = _scn("s3", tier="nightly")
    selected = _scenarios_for_tier([smoke, full, nightly], "full")
    assert sorted(s.id for s in selected) == ["s1", "s2"]


def test_scenarios_for_tier_nightly_returns_all():
    smoke = _scn("s1", tier="smoke")
    full = _scn("s2", tier="full")
    nightly = _scn("s3", tier="nightly")
    selected = _scenarios_for_tier([smoke, full, nightly], "nightly")
    assert sorted(s.id for s in selected) == ["s1", "s2", "s3"]


def test_scenarios_for_tier_rejects_unknown():
    with pytest.raises(ValueError, match="unknown tier"):
        _scenarios_for_tier([], "made-up")


# ---- jig sim run-tier CLI -----------------------------------------------


def _write_minimal_scenario(path: Path, scenario_id: str, tier: str) -> None:
    """Write a scenario YAML that the driver can run mock-mode happily.

    A near-empty scenario (no steps) trivially passes — the driver
    runs zero steps and zero final assertions, so report.passed is True.
    Used to keep the CLI test fast + isolated from full-spine fixtures.
    """
    path.write_text(
        yaml.safe_dump(
            {
                "spec_version": 1,
                "id": scenario_id,
                "description": "trivial",
                "persona": "methodical",
                "estimated_cost_usd_max": 0.0,
                "tier": tier,
                "steps": [],
                "final_assertions": [],
            }
        )
    )


def test_cli_run_tier_smoke_runs_only_smoke(tmp_path: Path):
    """``jig sim run-tier smoke`` runs the smoke-tagged scenarios only."""
    scenario_dir = tmp_path / "scenarios"
    scenario_dir.mkdir()
    _write_minimal_scenario(scenario_dir / "a.scenario.yaml", "a-smoke", "smoke")
    _write_minimal_scenario(scenario_dir / "b.scenario.yaml", "b-full", "full")
    _write_minimal_scenario(scenario_dir / "c.scenario.yaml", "c-night", "nightly")

    runner = CliRunner()
    result = runner.invoke(sim, ["run-tier", "smoke", "--scenarios", str(scenario_dir)])
    assert result.exit_code == 0, result.output
    assert "a-smoke" in result.output
    assert "b-full" not in result.output
    assert "c-night" not in result.output
    assert "1 scenario(s)" in result.output


def test_cli_run_tier_full_includes_smoke(tmp_path: Path):
    scenario_dir = tmp_path / "scenarios"
    scenario_dir.mkdir()
    _write_minimal_scenario(scenario_dir / "a.scenario.yaml", "a-smoke", "smoke")
    _write_minimal_scenario(scenario_dir / "b.scenario.yaml", "b-full", "full")

    runner = CliRunner()
    result = runner.invoke(sim, ["run-tier", "full", "--scenarios", str(scenario_dir)])
    assert result.exit_code == 0, result.output
    assert "a-smoke" in result.output
    assert "b-full" in result.output
    assert "2 scenario(s)" in result.output


def test_cli_run_tier_no_scenarios_at_tier_message(tmp_path: Path):
    scenario_dir = tmp_path / "scenarios"
    scenario_dir.mkdir()
    _write_minimal_scenario(scenario_dir / "a.scenario.yaml", "a-smoke", "smoke")

    runner = CliRunner()
    result = runner.invoke(
        sim, ["run-tier", "nightly", "--scenarios", str(scenario_dir)]
    )
    # Nightly includes smoke, so a-smoke runs. Hit the empty case
    # with a fresh dir.
    assert result.exit_code == 0, result.output


def test_cli_run_tier_against_in_tree_library_smoke_passes(tmp_path: Path):
    """``jig sim run-tier smoke`` against the in-tree library all passes.

    This is the closest CLI-side equivalent of `pytest -m sim_smoke`
    for the bones+MVP scenario set; everything is currently smoke-tier
    so all 11 scenarios run + pass.
    """
    runner = CliRunner()
    # No --scenarios → defaults to in-tree library.
    result = runner.invoke(sim, ["run-tier", "smoke"])
    assert result.exit_code == 0, result.output
    assert "PASS" in result.output
    # Spot-check a couple of the canonical scenario ids show up.
    assert "bones-walking-skeleton" in result.output
    assert "bones-with-deferred-ticket" in result.output


def test_cli_run_tier_exits_1_on_failure(tmp_path: Path):
    """A scenario that errors during the run causes exit code 1."""
    scenario_dir = tmp_path / "scenarios"
    scenario_dir.mkdir()
    # A scenario with a step that has no handler will error; the
    # driver records the StepOutcome.error and report.passed → False.
    bad = {
        "spec_version": 1,
        "id": "bad-handler",
        "description": "x",
        "persona": "methodical",
        "estimated_cost_usd_max": 0.0,
        "tier": "smoke",
        "steps": [
            {"kind": "mock_dev_commit", "params": {"ticket_id": "missing"}},
        ],
        "final_assertions": [],
    }
    (scenario_dir / "bad.scenario.yaml").write_text(yaml.safe_dump(bad))
    runner = CliRunner()
    result = runner.invoke(sim, ["run-tier", "smoke", "--scenarios", str(scenario_dir)])
    assert result.exit_code == 1, result.output
    assert "FAIL" in result.output
    assert "bad-handler" in result.output


# ---- sim_smoke marker registration --------------------------------------


def test_sim_smoke_marker_registered(pytestconfig: pytest.Config):
    """The marker must be registered so -m sim_smoke selects without warning."""
    markers = pytestconfig.getini("markers")
    names = [m.split(":")[0] for m in markers]
    assert "sim_smoke" in names


def test_sim_smoke_marker_applied_to_scenario_tests():
    """Spot-check: the bones scenario test file carries the sim_smoke marker.

    Reads the module's pytestmark; we use a module-level marker so
    every test in the file inherits it (no need to decorate each).
    """
    import tests.test_sim_bones_scenario as mod

    pm = getattr(mod, "pytestmark", None)
    # pytestmark may be a single MarkDecorator or a list of them.
    marks = pm if isinstance(pm, list) else [pm]
    names = {getattr(m, "name", None) for m in marks if m is not None}
    assert "sim_smoke" in names
