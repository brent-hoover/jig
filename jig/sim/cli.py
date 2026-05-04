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

from jig.sim.coverage import (
    CoverageThreshold,
    compute_coverage,
    compute_coverage_with_threshold,
    format_coverage,
)
from jig.sim.driver import Driver, ScenarioReport
from jig.sim.realism import RealismGap, list_gaps, log_gap
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


# Tier ordering: a higher-tier run also runs every lower-tier
# scenario. ``smoke`` ⊂ ``full`` ⊂ ``nightly``. The list ordering
# determines what ``run-tier <tier>`` executes (every tier whose index
# is ≤ the requested tier's index).
_TIER_ORDER: tuple[str, ...] = ("smoke", "full", "nightly")


def _scenarios_for_tier(scenarios: list[Scenario], tier: str) -> list[Scenario]:
    """Filter the library to scenarios at or below ``tier``."""
    if tier not in _TIER_ORDER:
        raise ValueError(
            f"unknown tier {tier!r}; expected one of {_TIER_ORDER!r}"
        )
    cutoff = _TIER_ORDER.index(tier)
    allowed = set(_TIER_ORDER[: cutoff + 1])
    return [s for s in scenarios if s.tier in allowed]


@sim.command(name="run-tier")
@click.argument("tier", type=click.Choice(list(_TIER_ORDER)))
@click.option(
    "--scenarios",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help=(
        "Path to a scenario file or directory. Defaults to the in-tree "
        "scenario library (tests/scenarios/)."
    ),
)
@click.option(
    "--enforce-threshold",
    type=float,
    default=None,
    help=(
        "After the tier run, gate on canonical-tag coverage. Exit 1 if "
        "covered-tags percentage falls below the given value. Per the "
        "v2-plan, the Final target is 80."
    ),
)
def run_tier(
    tier: str, scenarios: Path | None, enforce_threshold: float | None
) -> None:
    """Run every scenario at the given tier (or below).

    Tier ordering: ``smoke`` ⊂ ``full`` ⊂ ``nightly``. ``run-tier full``
    executes every smoke + full scenario; ``run-tier nightly`` runs
    all three. All runs are mock-mode (no LLM cost) — real-mode
    requires the explicit ``jig sim run --real`` invocation.

    Exits 1 if any scenario fails. With ``--enforce-threshold N``,
    also exits 1 if covered-tag percentage falls below N.
    """
    src = scenarios if scenarios is not None else _DEFAULT_SCENARIO_DIR
    library = _load_scenario_library(src)
    selected = _scenarios_for_tier(library, tier)
    if not selected:
        click.echo(f"No scenarios at tier {tier!r}; nothing to run.")
        return

    click.echo(f"Running {len(selected)} scenario(s) at tier {tier!r}\n")
    failed: list[str] = []
    with tempfile.TemporaryDirectory(prefix="jig-sim-tier-") as tmp_root:
        for scn in selected:
            click.echo(f"--- {scn.id} (tier={scn.tier})")
            # Per-scenario tmp dir so cross-scenario state doesn't bleed.
            scn_root = Path(tmp_root) / scn.id
            scn_root.mkdir(parents=True, exist_ok=True)
            _seed_repo(scn_root)
            driver = Driver()
            report = asyncio.run(driver.run(scn, project_root=scn_root))
            if report.passed:
                click.echo(f"    PASS ({len(report.step_outcomes)} steps)")
            else:
                failed.append(scn.id)
                click.echo("    FAIL")
                click.echo(report.failure_summary())

    click.echo(f"\nTier {tier!r}: {len(selected) - len(failed)}/{len(selected)} passed")
    threshold_failed = False
    if enforce_threshold is not None:
        # The threshold floor is computed against the full library, not
        # just the tier-selected subset, so a smoke-tier run still
        # surfaces the canonical taxonomy's coverage state. Operators
        # gating on a tier-restricted floor can pass --scenarios a
        # tier-filtered dir.
        report = compute_coverage_with_threshold(
            library, CoverageThreshold(min_percent=enforce_threshold)
        )
        gate = "PASS" if report.meets_threshold else "FAIL"
        click.echo(
            f"Coverage: {report.coverage_percent:.2f}% "
            f"(threshold {enforce_threshold:.2f}%) → {gate}"
        )
        if not report.meets_threshold:
            threshold_failed = True
    if failed:
        click.echo(f"Failed: {', '.join(failed)}")
        sys.exit(1)
    if threshold_failed:
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
@click.option(
    "--threshold",
    type=float,
    default=None,
    help=(
        "Minimum canonical-tag coverage percentage to require. When set, "
        "the CLI exits 1 if the library covers fewer than ``--threshold`` "
        "percent of canonical tags. Default: no threshold (informational)."
    ),
)
def coverage(scenarios: Path | None, threshold: float | None) -> None:
    """Print a markdown coverage report across the scenario library.

    Aggregates ``coverage_tags`` per scenario against the canonical
    taxonomy in ``jig.sim.coverage.CANONICAL_TAGS``. Surfaces gap
    tags (canonical tags that no scenario claims) so the operator
    knows what to write next.

    Pass ``--threshold N`` to gate on coverage — the CLI exits 1 when
    the library's covered-tags percentage falls below ``N``.
    """
    src = scenarios if scenarios is not None else _DEFAULT_SCENARIO_DIR
    library = _load_scenario_library(src)
    if threshold is not None:
        report = compute_coverage_with_threshold(
            library, CoverageThreshold(min_percent=threshold)
        )
        click.echo(format_coverage(report))
        if not report.meets_threshold:
            sys.exit(1)
    else:
        report = compute_coverage(library)
        click.echo(format_coverage(report))


@sim.group(name="realism")
def realism() -> None:
    """Realism-budget logging surface (MVP: log + list)."""


@realism.command(name="log")
@click.argument("description")
@click.option(
    "--kind",
    default="general",
    help=(
        "Short label categorizing the gap (e.g. 'ambiguous-confirmation'). "
        "Free-form for MVP; Final formalizes a taxonomy."
    ),
)
@click.option(
    "--persona",
    default=None,
    help=(
        "Optional id of the existing persona whose profile should grow "
        "to cover this behavior. Triage tooling reads this hint."
    ),
)
@click.option(
    "--source",
    type=click.Choice(["operator", "real-run"]),
    default="operator",
    help="Where the gap was observed.",
)
@click.option(
    "--project-root",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=Path.cwd,
    help=(
        "Project root the gap is logged under. Defaults to CWD; gaps "
        "land at <root>/.jig/sim/realism-gaps.jsonl."
    ),
)
def realism_log(
    description: str,
    kind: str,
    persona: str | None,
    source: str,
    project_root: Path,
) -> None:
    """Log one realism gap to ``.jig/sim/realism-gaps.jsonl``."""
    gap = RealismGap(
        kind=kind,
        description=description,
        source=source,  # type: ignore[arg-type]
        persona_to_extend=persona,
    )
    gap_id = asyncio.run(log_gap(project_root, gap))
    click.echo(f"logged gap {gap_id} ({kind}) at {gap.observed_at.isoformat()}")


@realism.command(name="list")
@click.option(
    "--project-root",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=Path.cwd,
    help="Project root to read gaps from. Defaults to CWD.",
)
def realism_list(project_root: Path) -> None:
    """Print every logged realism gap, chronologically."""
    gaps = asyncio.run(list_gaps(project_root))
    if not gaps:
        click.echo("(no realism gaps logged)")
        return
    for gap in gaps:
        persona = (
            f" → extend persona '{gap.persona_to_extend}'"
            if gap.persona_to_extend
            else ""
        )
        click.echo(
            f"[{gap.observed_at.isoformat()}] ({gap.source}/{gap.kind}){persona}: "
            f"{gap.description}"
        )


# ---- regression scenario CLI (Track H Final) ----------------------------


# Default location for scaffolded regression scenarios. Lives under
# tests/scenarios/regressions/ per design.md §"Failure-mode regression
# scenarios" — the convention is "regression scenarios live in their
# own subdirectory; CI fails if any regression scenario passes when
# the bug is reintroduced."
_REGRESSIONS_SUBDIR = "regressions"


def _regressions_dir(scenarios_root: Path) -> Path:
    return scenarios_root / _REGRESSIONS_SUBDIR


def _regression_template(
    *, bug_id: str, description: str, persona: str
) -> dict:
    """Minimal scenario YAML payload for a brand-new regression scenario.

    Operators edit this in place after scaffolding — the steps list is
    intentionally near-empty so the scenario fails loud until the
    operator authors the bug-reproducing payload.
    """
    return {
        "spec_version": 1,
        "id": f"regression-{bug_id}",
        "description": description,
        "persona": persona,
        "estimated_cost_usd_max": 0.0,
        "tier": "smoke",
        "coverage_tags": [
            "regression-scenarios",
        ],
        "steps": [],
        "final_assertions": [],
    }


@sim.group(name="regression")
def regression() -> None:
    """Regression scenario discipline (Track H Final).

    Per ``docs/synthetic-operator/design.md`` §"Failure-mode regression
    scenarios", every bug fix lands with a regression scenario that
    would have caught it. This command group scaffolds + lists those.
    """


@regression.command(name="new")
@click.option(
    "--bug-id",
    required=True,
    help="Short id for the bug (e.g. 'cascade-overlap'). Becomes the filename.",
)
@click.option(
    "--description",
    required=True,
    help="One-line description of the bug + what the regression scenario covers.",
)
@click.option(
    "--persona",
    default="methodical",
    help="Persona the regression scenario uses. Defaults to methodical.",
)
@click.option(
    "--from-scenario",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help=(
        "Optional path to an existing scenario to clone. The bug-id + "
        "description override the cloned id + description; coverage_tags "
        "get a regression-{bug-id} marker appended."
    ),
)
@click.option(
    "--scenarios",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help=(
        "Scenario library root. Defaults to the in-tree library. The "
        "regression file lands at <root>/regressions/<bug-id>.scenario.yaml."
    ),
)
@click.option(
    "--realism-gap-id",
    default=None,
    help=(
        "Optional id of the realism-gap that originated this regression. "
        "Recorded in the scaffolded YAML's header comment block."
    ),
)
def regression_new(
    bug_id: str,
    description: str,
    persona: str,
    from_scenario: Path | None,
    scenarios: Path | None,
    realism_gap_id: str | None,
) -> None:
    """Scaffold a new regression scenario YAML at <root>/regressions/<bug-id>.

    Inherits structure from ``--from-scenario`` if given, otherwise
    starts from a minimal template. Tags ``regression-{bug-id}``
    automatically; includes a header comment block with the bug id +
    description + (optional) realism-gap reference.
    """
    import yaml as yaml_mod

    src_root = scenarios if scenarios is not None else _DEFAULT_SCENARIO_DIR
    out_dir = _regressions_dir(src_root)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{bug_id}.scenario.yaml"
    if out_path.exists():
        click.echo(
            f"refused to overwrite existing scenario at {out_path}", err=True
        )
        sys.exit(1)

    if from_scenario is not None:
        # Cloning preserves the structure of an existing scenario; we
        # patch id + description and append the regression coverage tag.
        loaded = load_scenario(from_scenario)
        payload = loaded.model_dump(mode="json")
        payload["id"] = f"regression-{bug_id}"
        payload["description"] = description
        regression_tag = "regression-scenarios"
        existing_tags = list(payload.get("coverage_tags") or [])
        if regression_tag not in existing_tags:
            existing_tags.append(regression_tag)
        payload["coverage_tags"] = existing_tags
    else:
        payload = _regression_template(
            bug_id=bug_id, description=description, persona=persona
        )

    header_lines = [
        f"# Regression scenario for bug: {bug_id}",
        f"# Description: {description}",
    ]
    if realism_gap_id:
        header_lines.append(f"# Originating realism-gap: {realism_gap_id}")
    header_lines.append(
        "# Per docs/synthetic-operator/design.md §'Failure-mode regression "
        "scenarios':"
    )
    header_lines.append(
        "#   every bug lands with one regression scenario that would have "
        "caught it."
    )
    header_lines.append("")

    body = yaml_mod.safe_dump(payload, sort_keys=False)
    out_path.write_text("\n".join(header_lines) + body)

    click.echo(f"scaffolded regression scenario at {out_path}")


@regression.command(name="list")
@click.option(
    "--scenarios",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help=(
        "Scenario library root. Defaults to the in-tree library. Lists "
        "every *.scenario.yaml under <root>/regressions/."
    ),
)
def regression_list(scenarios: Path | None) -> None:
    """List every regression scenario with its bug id + status."""
    src_root = scenarios if scenarios is not None else _DEFAULT_SCENARIO_DIR
    rdir = _regressions_dir(src_root)
    if not rdir.is_dir():
        click.echo("(no regressions/ directory found; nothing to list)")
        return
    files = sorted(rdir.rglob("*.scenario.yaml"))
    if not files:
        click.echo("(no regression scenarios found)")
        return
    for path in files:
        try:
            scn = load_scenario(path)
        except Exception as e:
            click.echo(
                f"  {path.name}: ERROR ({type(e).__name__}: {e})", err=True
            )
            continue
        # Bug id is whatever follows "regression-" in the scenario id; if
        # the operator renamed they get the literal id.
        bug_id = (
            scn.id.removeprefix("regression-")
            if scn.id.startswith("regression-")
            else scn.id
        )
        # Status heuristic: "scaffold" when steps list is empty; "ready"
        # otherwise. Keeps the list useful without re-running the
        # scenarios.
        status = "scaffold" if not scn.steps else "ready"
        click.echo(f"  {bug_id} [{status}]: {scn.description.splitlines()[0]}")


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
