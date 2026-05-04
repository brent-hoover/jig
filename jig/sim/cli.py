"""``jig sim`` CLI surface (Track H5 bones + Track H MVP).

Bones shipped ``jig sim run <scenario.yaml>``. Track H MVP added
``coverage`` (aggregate coverage report across the scenario library)
and the realism-budget + run-tier surfaces.

Two run modes for ``jig sim run``:

- **mock** (default; CI-safe; no LLM cost) — the dev step is replaced
  with a deterministic helper that satisfies the bones reviewer's
  checks. This is what ``test_sim_bones_scenario.py`` exercises.
- **real** (operator-invoked; bones cost target < $1, runtime < 10
  min per ``docs/implementation/v2-plan.md``) — the dev step spawns a
  real Claude agent via the orchestrator's existing dispatch path
  rooted at the simulator's tmp project. Requires
  ``CLAUDE_CODE_OAUTH_TOKEN`` to be set (run ``claude setup-token``
  if not). The CLI prompts before invoking real-mode so an operator
  can't fat-finger themselves into spending money — pass ``--yes`` to
  skip the prompt (intended for scripts).

Per-run isolation: the CLI creates its own tmp dir under the system
temp root for each run and never reuses it, mirroring the
per-pytest-fixture isolation tests get from ``tmp_path``.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import click

from jig.sim.coverage import compute_coverage, format_coverage
from jig.sim.driver import Driver, ScenarioReport
from jig.sim.scenario import Scenario, load_scenario

__all__ = ["sim"]


# Default scenario library: the bundled scenarios live under
# ``tests/scenarios/`` (versioned with the test suite — they're the
# fixtures the in-tree sim tests run). Operators with their own
# scenario sets pass ``--scenarios <dir>`` to point elsewhere.
_DEFAULT_SCENARIO_DIR = (
    Path(__file__).resolve().parents[2] / "tests" / "scenarios"
)


def _load_scenario_library(path: Path) -> list[Scenario]:
    """Load every ``*.scenario.yaml`` under ``path`` (recursive).

    Returns scenarios sorted by id for deterministic CLI output.
    Empty directory → empty list (compute_coverage handles that).
    """
    if path.is_file():
        return [load_scenario(path)]
    scenarios: list[Scenario] = []
    for src in sorted(path.rglob("*.scenario.yaml")):
        scenarios.append(load_scenario(src))
    scenarios.sort(key=lambda s: s.id)
    return scenarios


def _under_pytest() -> bool:
    """True when the CLI is being driven by pytest (skip interactive prompts)."""
    return "PYTEST_CURRENT_TEST" in os.environ


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
@click.option(
    "--yes",
    is_flag=True,
    default=False,
    help=(
        "Skip the real-mode confirmation prompt. Intended for scripts; "
        "interactive operators should let the prompt run so they don't "
        "fat-finger an LLM bill."
    ),
)
def run(scenario: Path, real: bool, yes: bool) -> None:
    """Run one scenario in mock mode (default) or real mode."""
    scn = load_scenario(scenario)
    if real:
        if not _confirm_real_mode(scn.estimated_cost_usd_max, assume_yes=yes):
            click.echo("Aborted; real-mode invocation cancelled.")
            sys.exit(1)
        if not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
            # Surface the missing-token failure mode early — the SDK
            # would eventually fail with a less-obvious error. The
            # operator's fix is `claude setup-token`.
            click.echo(
                "Warning: CLAUDE_CODE_OAUTH_TOKEN is not set. Real-mode "
                "agent spawn will fail without it. Run `claude setup-token` "
                "and export it (e.g. via ~/.secrets.env) before retrying.",
                err=True,
            )
    with tempfile.TemporaryDirectory(prefix="jig-sim-") as tmpdir:
        root = Path(tmpdir)
        _seed_repo(root)
        driver = Driver(real_mode=real)
        report = asyncio.run(driver.run(scn, project_root=root))
        _print_report(report)
        if not report.passed:
            sys.exit(1)


@sim.command(name="coverage")
@click.option(
    "--scenarios",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help=(
        "Path to a scenario file or directory. Defaults to the in-tree "
        "scenario library (tests/scenarios/). Operators with their own "
        "scenarios pass a directory."
    ),
)
def coverage(scenarios: Path | None) -> None:
    """Print a markdown coverage report across the scenario library.

    Aggregates ``coverage_tags`` per scenario against the canonical
    taxonomy in ``jig.sim.coverage.CANONICAL_TAGS``. Surfaces gap
    tags (canonical tags that no scenario claims) so the operator
    knows what to write next.
    """
    src = scenarios if scenarios is not None else _DEFAULT_SCENARIO_DIR
    library = _load_scenario_library(src)
    report = compute_coverage(library)
    click.echo(format_coverage(report))


def _confirm_real_mode(estimated_cost_usd_max: float, *, assume_yes: bool) -> bool:
    """Prompt the operator before kicking off real-mode.

    Skipped when running under pytest (so test runs don't deadlock on
    stdin) or when ``--yes`` was passed. Defaults to NO so a stray
    Enter cancels the run instead of authorizing spend.
    """
    if assume_yes or _under_pytest():
        return True
    click.echo(
        f"Real-mode invocation will spawn a Claude Code dev agent and "
        f"incur LLM costs (scenario budget: ~${estimated_cost_usd_max:.2f}). "
        "Continue? [y/N] ",
        nl=False,
    )
    answer = click.get_text_stream("stdin").readline().strip().lower()
    return answer in ("y", "yes")
