"""Coverage metrics for the scenario library (Track H MVP follow-on).

Tests the canonical-tag taxonomy + ``compute_coverage`` aggregation +
``format_coverage`` markdown rendering + the ``jig sim coverage`` CLI.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner
from pydantic import ValidationError

from jig.sim.cli import sim
from jig.sim.coverage import (
    CANONICAL_TAGS,
    CoverageReport,
    TagCoverage,
    compute_coverage,
    format_coverage,
)
from jig.sim.scenario import Scenario, load_scenario


# ---- canonical taxonomy --------------------------------------------------


def test_canonical_tags_has_known_categories():
    """Spot-check the load-bearing categories from the task taxonomy."""
    for tag in (
        "po-l0", "po-l1", "po-l2", "po-l3",
        "sa-bones", "sa-incremental", "sa-risks",
        "sa-spike-mitigated", "sa-spike-confirmed-impossible", "sa-cascade",
        "pm-planner", "pm-coordinator-bones", "pm-coordinator-multi-layer",
        "pm-deferred",
        "dev-mock", "dev-real",
        "reviewer-contract-compliance",
        "reviewer-cross-cutting-policy",
        "reviewer-spec-compliance",
        "reviewer-intent-compliance",
    ):
        assert tag in CANONICAL_TAGS


def test_canonical_tags_is_frozen():
    """The taxonomy is a frozenset so callers can't mutate it."""
    assert isinstance(CANONICAL_TAGS, frozenset)


# ---- Scenario.coverage_tags validation -----------------------------------


def test_scenario_rejects_unknown_coverage_tag(tmp_path: Path):
    """Typos in coverage_tags fail at load time, not silently."""
    src = tmp_path / "bad.scenario.yaml"
    src.write_text(
        yaml.safe_dump(
            {
                "spec_version": 1,
                "id": "bad",
                "description": "x",
                "persona": "methodical",
                "estimated_cost_usd_max": 0.0,
                "coverage_tags": ["po-l9-typo"],
                "steps": [],
                "final_assertions": [],
            }
        )
    )
    with pytest.raises(ValidationError):
        load_scenario(src)


def test_scenario_accepts_known_coverage_tag(tmp_path: Path):
    src = tmp_path / "ok.scenario.yaml"
    src.write_text(
        yaml.safe_dump(
            {
                "spec_version": 1,
                "id": "ok",
                "description": "x",
                "persona": "methodical",
                "estimated_cost_usd_max": 0.0,
                "coverage_tags": ["po-l0", "dev-mock"],
                "steps": [],
                "final_assertions": [],
            }
        )
    )
    scn = load_scenario(src)
    assert scn.coverage_tags == ["po-l0", "dev-mock"]


def test_scenario_coverage_tags_default_empty(tmp_path: Path):
    src = tmp_path / "nox.scenario.yaml"
    src.write_text(
        yaml.safe_dump(
            {
                "spec_version": 1,
                "id": "nox",
                "description": "x",
                "persona": "methodical",
                "estimated_cost_usd_max": 0.0,
                "steps": [],
                "final_assertions": [],
            }
        )
    )
    scn = load_scenario(src)
    assert scn.coverage_tags == []


# ---- compute_coverage ----------------------------------------------------


def _scn(scenario_id: str, tags: list[str]) -> Scenario:
    return Scenario(
        id=scenario_id,
        description="x",
        persona="methodical",
        estimated_cost_usd_max=0.0,
        coverage_tags=tags,
    )


def test_compute_coverage_empty_library():
    report = compute_coverage([])
    assert report.total_scenarios == 0
    assert all(not tc.covered for tc in report.per_tag.values())
    assert sorted(report.gap_tags) == sorted(CANONICAL_TAGS)
    assert report.covered_tags == []


def test_compute_coverage_single_scenario():
    scn = _scn("s1", ["po-l0", "dev-mock"])
    report = compute_coverage([scn])
    assert report.total_scenarios == 1
    assert "po-l0" in report.covered_tags
    assert "dev-mock" in report.covered_tags
    assert report.per_tag["po-l0"].scenario_ids == ["s1"]
    assert report.per_tag["po-l0"].count == 1


def test_compute_coverage_aggregates_multiple_scenarios():
    """Two scenarios claiming the same tag aggregate; ids deduped + sorted."""
    s1 = _scn("s1", ["po-l0", "po-l3"])
    s2 = _scn("s2", ["po-l0", "sa-bones"])
    report = compute_coverage([s1, s2])
    assert report.per_tag["po-l0"].scenario_ids == ["s1", "s2"]
    assert report.per_tag["po-l3"].scenario_ids == ["s1"]
    assert report.per_tag["sa-bones"].scenario_ids == ["s2"]


def test_compute_coverage_unknown_tag_surfaces():
    """Tags outside CANONICAL_TAGS land in unknown_tags (defensive)."""
    # Bypass schema validation by constructing the scenario via dict
    # then mutating coverage_tags (the validator runs on init only).
    scn = _scn("s1", ["po-l0"])
    scn.__dict__["coverage_tags"] = ["po-l0", "made-up-tag"]
    report = compute_coverage([scn])
    assert "made-up-tag" in report.unknown_tags
    assert "po-l0" in report.covered_tags


# ---- format_coverage -----------------------------------------------------


def test_format_coverage_renders_markdown_sections():
    s1 = _scn("s1", ["po-l0", "dev-mock"])
    report = compute_coverage([s1])
    out = format_coverage(report)
    assert "# Scenario coverage report" in out
    assert "## Covered tags" in out
    assert "## Gap tags" in out
    assert "po-l0" in out
    assert "s1" in out


def test_format_coverage_empty_library_marks_all_gaps():
    report = compute_coverage([])
    out = format_coverage(report)
    assert "no tags covered" in out
    # Every canonical tag should appear under gap tags.
    for tag in CANONICAL_TAGS:
        assert tag in out


def test_format_coverage_no_gaps_message():
    """When every canonical tag is covered, the gaps section says so."""
    scenarios = [_scn(f"s-{tag}", [tag]) for tag in CANONICAL_TAGS]
    report = compute_coverage(scenarios)
    out = format_coverage(report)
    assert "every canonical tag is covered" in out


# ---- CLI integration -----------------------------------------------------


def test_cli_coverage_runs_against_default_library():
    """``jig sim coverage`` hits the in-tree scenario library."""
    runner = CliRunner()
    result = runner.invoke(sim, ["coverage"])
    assert result.exit_code == 0, result.output
    assert "# Scenario coverage report" in result.output
    # The in-tree library covers po-l0 (every scenario starts there).
    assert "po-l0" in result.output


def test_cli_coverage_against_explicit_dir(tmp_path: Path):
    """``--scenarios <dir>`` points the CLI at a custom library."""
    # One scenario file in the dir.
    src = tmp_path / "x.scenario.yaml"
    src.write_text(
        yaml.safe_dump(
            {
                "spec_version": 1,
                "id": "x",
                "description": "x",
                "persona": "methodical",
                "estimated_cost_usd_max": 0.0,
                "coverage_tags": ["po-l0"],
                "steps": [],
                "final_assertions": [],
            }
        )
    )
    runner = CliRunner()
    result = runner.invoke(sim, ["coverage", "--scenarios", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "Scenarios analyzed: 1" in result.output
    assert "po-l0" in result.output


def test_cli_coverage_against_empty_dir(tmp_path: Path):
    runner = CliRunner()
    result = runner.invoke(sim, ["coverage", "--scenarios", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "Scenarios analyzed: 0" in result.output


# ---- TagCoverage model ---------------------------------------------------


def test_tag_coverage_covered_property():
    tc = TagCoverage(tag="po-l0", scenario_ids=["s1"])
    assert tc.covered
    assert tc.count == 1
    empty = TagCoverage(tag="dev-real")
    assert not empty.covered
    assert empty.count == 0


def test_coverage_report_round_trips_through_json():
    scn = _scn("s1", ["po-l0"])
    report = compute_coverage([scn])
    payload = report.model_dump(mode="json")
    restored = CoverageReport.model_validate(payload)
    assert restored.total_scenarios == 1
    assert restored.per_tag["po-l0"].scenario_ids == ["s1"]
