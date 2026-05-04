"""``jig sim`` CLI surface (Track H5, bones).

Bones ships ``jig sim run <scenario.yaml>`` only — the minimum CLI the
operator needs to run the bones-walking-skeleton scenario manually.
The full ``jig sim`` surface from ``docs/synthetic-operator/design.md``
(``run-tier``, ``coverage``, ``realism``) lands in MVP/Final.

Two run modes:

- **mock** (default; CI-safe; no LLM cost) — the dev step is replaced
  with a deterministic helper that satisfies the bones reviewer's
  checks. This is what ``test_sim_bones_scenario.py`` exercises.
- **real** (operator-invoked; bones cost target < $1) — the dev step
  spawns a real Claude agent against the project_root. NOT yet wired
  in this commit; the flag is accepted so the operator-facing surface
  is stable, but the implementation will land alongside the
  orchestrator-side dev-spawn glue. Calling ``--real`` raises
  NotImplementedError until then.

Per-run isolation: the CLI creates its own tmp dir under the system
temp root for each run and never reuses it, mirroring the
per-pytest-fixture isolation tests get from ``tmp_path``.
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
import tempfile
from pathlib import Path

import click

from jig.sim.driver import Driver, ScenarioReport
from jig.sim.scenario import load_scenario

__all__ = ["sim"]


def _seed_repo(root: Path) -> None:
    """Initialize a fresh git repo at ``root`` for the worktree-diff path.

    The bones contract-compliance reviewer reads
    ``git diff main..HEAD`` from the worktree; without a base commit
    there's nothing to diff against. We seed a single commit so the
    reviewer has its baseline. Identical to the test-side _seed_repo;
    duplicated here rather than imported because tests aren't part of
    the package distribution.
    """
    subprocess.run(
        ["git", "init", "-b", "main"], cwd=root, check=True, capture_output=True
    )
    for k, v in (
        ("user.email", "sim@jig"),
        ("user.name", "jig sim"),
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


def _print_report(report: ScenarioReport) -> None:
    """Operator-facing run summary. PASS / FAIL plus per-failure detail.

    Bones keeps this minimal — one line per step + the failure summary
    on FAIL. Richer report.yaml + artifacts/ archive land with H9
    (coverage) + H10 (realism budget).
    """
    click.echo(f"\nScenario: {report.scenario_id}")
    click.echo(f"Steps: {len(report.step_outcomes)}")
    click.echo(f"Captured events: {len(report.captured_events)}")
    click.echo(f"Cost (USD): {report.cost_usd:.4f}")
    for s in report.step_outcomes:
        marker = "PASS" if s.passed else "FAIL"
        click.echo(f"  [{marker}] {s.kind}")
    if report.passed:
        click.echo("\nResult: PASS")
    else:
        click.echo("\nResult: FAIL")
        click.echo(report.failure_summary())


@click.group()
def sim() -> None:
    """Synthetic operator simulator (bones-scope: ``run`` only)."""


@sim.command(name="run")
@click.argument("scenario", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--real",
    is_flag=True,
    default=False,
    help=(
        "Run with a real dev-agent step instead of the mock helper. "
        "Costs LLM tokens; bones budget is < $1 for the walking-"
        "skeleton scenario. Operator-invoked only."
    ),
)
def run(scenario: Path, real: bool) -> None:
    """Run one scenario in mock mode (default) or real mode."""
    if real:
        raise click.ClickException(
            "--real not yet implemented for bones; mock mode is the "
            "bones milestone. Real-mode dev dispatch lands with the "
            "orchestrator-side spawn glue."
        )
    scn = load_scenario(scenario)
    with tempfile.TemporaryDirectory(prefix="jig-sim-") as tmpdir:
        root = Path(tmpdir)
        _seed_repo(root)
        report = asyncio.run(Driver().run(scn, project_root=root))
        _print_report(report)
        if not report.passed:
            sys.exit(1)
