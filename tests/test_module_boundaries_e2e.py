"""End-to-end module boundaries (step 6).

Declares a boundary, generates the enforcement rules, then drives the REAL dev
commit gate (`commit_worktree`, with git + semgrep) against a worktree whose
code crosses the boundary — asserting the gate fails, then passes once the
forbidden import is removed. Skipped when semgrep is absent.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from jig.boundary_rules import generate_boundary_rules
from jig.worktree import BoundaryViolationError, commit_worktree

pytestmark = pytest.mark.skipif(
    shutil.which("semgrep") is None, reason="semgrep not installed"
)


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _setup_project_with_rules(tmp_path: Path) -> Path:
    """A project where module `job-posting` forbids importing `billing`, with
    the boundary rules generated to .jig/rules/semgrep/boundaries/."""
    jig = tmp_path / ".jig"
    (jig / "spec" / "modules" / "job-posting").mkdir(parents=True)
    (jig / "config.yaml").write_text(
        yaml.safe_dump(
            {"project": {"name": "my-ats", "id": "my-ats", "path": str(tmp_path)}}
        )
    )
    intent = {"problem": "p", "simplest_solution": "s"}
    (jig / "spec" / "architecture.yaml").write_text(
        yaml.safe_dump(
            {
                "modules": [
                    {"id": "job-posting", "title": "JP", "summary": "x", "intent": intent},
                    {"id": "billing", "title": "B", "summary": "x", "intent": intent},
                ]
            }
        )
    )
    (jig / "spec" / "modules" / "job-posting" / "boundaries.yaml").write_text(
        yaml.safe_dump(
            {"module": "job-posting", "internal": {"forbidden_modules": ["billing"]}}
        )
    )
    written = generate_boundary_rules(tmp_path)
    assert [p.name for p in written] == ["job-posting.yml"]
    return tmp_path


def _make_git_worktree(project: Path) -> Path:
    """A git repo at <project>/.jig/worktrees/t1 with the module package dir.
    (No pyproject.toml, so the gate's ruff step no-ops and we isolate the
    boundary check.)"""
    worktree = project / ".jig" / "worktrees" / "t1"
    (worktree / "src" / "my_ats" / "job_posting").mkdir(parents=True)
    _git(worktree, "init", "-q")
    _git(worktree, "config", "user.email", "t@t.t")
    _git(worktree, "config", "user.name", "t")
    (worktree / "README").write_text("seed\n")
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-qm", "seed")
    return worktree


async def test_e2e_forbidden_import_fails_gate_then_passes_when_fixed(tmp_path):
    project = _setup_project_with_rules(tmp_path)
    worktree = _make_git_worktree(project)
    code = worktree / "src" / "my_ats" / "job_posting" / "code.py"

    # Violation: job-posting imports forbidden sibling billing.
    code.write_text("import my_ats.billing\n")
    with pytest.raises(BoundaryViolationError) as exc:
        await commit_worktree(worktree, "feat: add code")
    assert any("billing" in v for v in exc.value.violations)

    # Fix: remove the forbidden import → gate passes and commits.
    code.write_text("VALUE = 1\n")
    result = await commit_worktree(worktree, "feat: add code")
    assert result.sha is not None
