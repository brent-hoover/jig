"""End-to-end tests for the test-rigor automated_checks wiring.

The ``pytest_diff`` helper itself is unit-tested in
``test_pytest_diff_helper.py``. These tests cover the integration
edges:

* The shipped catalog loads via the ``load_check_catalog`` fallback
  on a project with no ``.jig/checks.yaml``.
* Every shipped workflow's ``automated_checks`` references resolve
  against the shipped catalog (no typos, no missing names).
* ``ScriptedRunner`` propagates ``extra_env`` to the spawned
  subprocess — the diff helper needs ``JIG_TICKET_BASE``.
* A failing scripted check bounces the handoff through
  ``run_handoff_gate``.

Real ``uv run pytest`` execution against a sample project is not
exercised here — too slow for the unit suite, and the parser
already verifies the contract the helper exposes.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from jig.check_runner import ScriptedRunner
from jig.checks import CheckCatalog, ScriptedCheck, load_check_catalog
from jig.handoff_gate import run_handoff_gate
from jig.models import PhaseConfig, WorkflowConfig
from jig.persistence import _defaults_dir
from jig.store import MessageBus
from jig.store.check_results import CheckResultsStore
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.thread import Handoff
from jig.ticket import Ticket, WorkType
from tests._test_ticket import TICKET_AC_PLACEHOLDER


def test_shipped_catalog_loads_via_fallback(tmp_path: Path) -> None:
    """An empty project has no ``.jig/checks.yaml`` — the loader
    falls back to ``jig/defaults/checks.yaml`` and returns the
    shipped entries."""
    cat = load_check_catalog(tmp_path)
    assert cat.names() == [
        "mypy-strict",
        "pytest-all",
        "pytest-diff-tests",
        "pytest-new-tests-fail",
        "ruff-check",
    ]


def test_shipped_catalog_entries_are_scripted() -> None:
    """Every shipped catalog entry is a ``scripted`` check — agent
    checks would need different runner wiring."""
    cat = load_check_catalog(Path("/nonexistent"))
    for name in cat.names():
        check = cat.get(name)
        assert isinstance(check, ScriptedCheck), (
            f"{name} is {type(check).__name__}, expected ScriptedCheck"
        )


def test_shipped_workflows_reference_only_catalog_entries() -> None:
    """Cross-reference: every ``automated_checks`` name declared in
    a shipped workflow must resolve in the shipped catalog. Catches
    typos at test time rather than at handoff-gate time on a real
    project."""
    import yaml

    from jig.models import WorkflowConfig

    cat = load_check_catalog(Path("/nonexistent"))
    catalog_names = set(cat.names())
    workflows_dir = _defaults_dir() / "workflows"
    seen_workflows = 0
    for yaml_file in sorted(workflows_dir.glob("*.yaml")):
        data = yaml.safe_load(yaml_file.read_text())
        wf = WorkflowConfig.model_validate(data)
        for phase in wf.phases:
            for check_name in phase.automated_checks:
                assert check_name in catalog_names, (
                    f"{yaml_file.name} → phase {phase.name!r} references "
                    f"{check_name!r} not in shipped catalog"
                )
        seen_workflows += 1
    assert seen_workflows > 0, "no shipped workflows found — package-data wrong?"


async def test_scripted_runner_propagates_extra_env(tmp_path: Path) -> None:
    """The diff-scoped pytest helpers read ``JIG_TICKET_BASE`` from
    the environment to know what to diff against. Verify
    ``ScriptedRunner`` actually plumbs the env through to the
    subprocess."""
    worktree = tmp_path / "wt"
    worktree.mkdir()
    # A check that prints the env var so we can read it from output.
    catalog = CheckCatalog.model_validate(
        {
            "echo-env": {
                "type": "scripted",
                "command": 'echo "JIG_TICKET_BASE=${JIG_TICKET_BASE:-UNSET}"',
                "severity": "required",
                "timeout_s": 30,
            }
        }
    )
    results = CheckResultsStore(tmp_path / "results.jsonl")
    await results.load()
    runner = ScriptedRunner(
        catalog=catalog,
        results=results,
        worktree_path=worktree,
        extra_env={"JIG_TICKET_BASE": "origin/develop"},
    )
    # ``run_check`` writes a CheckResult; assert the captured stdout
    # shows the env var made it through.
    result = await runner.run_check(
        ticket_id="t-1",
        phase="implement",
        check_name="echo-env",
    )
    assert "JIG_TICKET_BASE=origin/develop" in result.output


async def test_scripted_runner_without_extra_env_inherits_environ(
    tmp_path: Path,
) -> None:
    """When ``extra_env`` is not provided, the subprocess still
    inherits ``os.environ`` (matching the prior default behaviour)."""
    import os

    worktree = tmp_path / "wt"
    worktree.mkdir()
    catalog = CheckCatalog.model_validate(
        {
            "echo-path": {
                "type": "scripted",
                "command": 'echo "PATH_PRESENT=${PATH:+yes}${PATH:-no}"',
                "severity": "required",
                "timeout_s": 30,
            }
        }
    )
    results = CheckResultsStore(tmp_path / "results.jsonl")
    await results.load()
    runner = ScriptedRunner(
        catalog=catalog,
        results=results,
        worktree_path=worktree,
    )
    result = await runner.run_check(
        ticket_id="t-1",
        phase="implement",
        check_name="echo-path",
    )
    # If PATH was inherited the subprocess prints "PATH_PRESENT=yes".
    assert "PATH_PRESENT=yes" in result.output
    # Sanity: the parent process did have PATH set.
    assert os.environ.get("PATH")


async def test_handoff_gate_bounces_when_scripted_check_fails(
    tmp_path: Path,
) -> None:
    """A scripted check that exits non-zero must produce a failing
    ``GateVerdict``. The orchestrator's bounce path runs off
    ``verdict.passing == False``."""
    # Worktree with a single commit so ``git rev-parse HEAD`` works
    # inside the runner's ``_git_head`` helper.
    worktree = tmp_path / "wt"
    worktree.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=worktree, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "--allow-empty",
            "-qm",
            "init",
        ],
        cwd=worktree,
        check=True,
    )

    tickets = TicketStore(tmp_path / "tickets.jsonl")
    await tickets.load()
    threads = ThreadStore(tmp_path / "comments.jsonl")
    await threads.load()
    results = CheckResultsStore(tmp_path / "results.jsonl")
    await results.load()
    bus = MessageBus(tmp_path / "messages.jsonl")
    await bus.load()

    tid = await tickets.create(
        Ticket(
            work_type=WorkType.FEATURE,
            title="t",
            created_by="orchestrator",
            workflow="custom-wf",
            description=TICKET_AC_PLACEHOLDER,
        )
    )
    hid = await threads.post(
        Handoff(
            ticket_id=tid,
            author="dev",
            phase="implement",
            outputs=[],
            summary="claim done — but tests fail",
        )
    )

    catalog = CheckCatalog.model_validate(
        {
            "guaranteed-fail": {
                "type": "scripted",
                "command": "exit 1",
                "severity": "required",
                "timeout_s": 30,
            }
        }
    )
    workflow = WorkflowConfig(
        name="custom-wf",
        phases=[
            PhaseConfig(
                name="implement",
                role="dev",
                task_template="t",
                acceptance_criteria="t",
                automated_checks=["guaranteed-fail"],
            ),
        ],
    )

    verdict = await run_handoff_gate(
        handoff_id=hid,
        tickets=tickets,
        threads=threads,
        results=results,
        catalog=catalog,
        workflow=workflow,
        worktree_path=worktree,
        project_path=tmp_path,
        bus=bus,
    )
    assert verdict.passing is False
    assert any(entry.check_name == "guaranteed-fail" for entry in verdict.failing), (
        f"expected guaranteed-fail in verdict.failing, got {verdict.failing}"
    )


async def test_handoff_gate_passes_when_scripted_check_succeeds(
    tmp_path: Path,
) -> None:
    """Complementary case — a passing scripted check produces a
    passing verdict. Pins the symmetric path."""
    worktree = tmp_path / "wt"
    worktree.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=worktree, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "--allow-empty",
            "-qm",
            "init",
        ],
        cwd=worktree,
        check=True,
    )

    tickets = TicketStore(tmp_path / "tickets.jsonl")
    await tickets.load()
    threads = ThreadStore(tmp_path / "comments.jsonl")
    await threads.load()
    results = CheckResultsStore(tmp_path / "results.jsonl")
    await results.load()
    bus = MessageBus(tmp_path / "messages.jsonl")
    await bus.load()

    tid = await tickets.create(
        Ticket(
            work_type=WorkType.FEATURE,
            title="t",
            created_by="orchestrator",
            workflow="custom-wf",
            description=TICKET_AC_PLACEHOLDER,
        )
    )
    hid = await threads.post(
        Handoff(
            ticket_id=tid,
            author="dev",
            phase="implement",
            outputs=[],
            summary="ready for review",
        )
    )

    catalog = CheckCatalog.model_validate(
        {
            "always-pass": {
                "type": "scripted",
                "command": "exit 0",
                "severity": "required",
                "timeout_s": 30,
            }
        }
    )
    workflow = WorkflowConfig(
        name="custom-wf",
        phases=[
            PhaseConfig(
                name="implement",
                role="dev",
                task_template="t",
                acceptance_criteria="t",
                automated_checks=["always-pass"],
            ),
        ],
    )

    verdict = await run_handoff_gate(
        handoff_id=hid,
        tickets=tickets,
        threads=threads,
        results=results,
        catalog=catalog,
        workflow=workflow,
        worktree_path=worktree,
        project_path=tmp_path,
        bus=bus,
    )
    assert verdict.passing is True
    assert verdict.failing == []
