"""Orchestrator auto-commit surfaces module-boundary degradation (roborev 410).

The second commit_worktree caller (_auto_commit_worktree) must honor the same
"degradation must not read as a clean pass" contract as handle_commit_progress:
when semgrep is missing/errors, the skipped enforcement is surfaced on the
ticket thread, not silently swallowed.
"""

from __future__ import annotations

import subprocess

import pytest
import yaml

from jig.boundary_rules import build_deny_rule
from jig.orchestrator import Orchestrator
from jig.store.threads import ThreadStore


@pytest.mark.asyncio
async def test_auto_commit_surfaces_boundary_degradation(tmp_path, monkeypatch):
    work = tmp_path / ".jig" / "worktrees" / "t1"
    pkg = work / "src" / "my_ats" / "job_posting"
    pkg.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=work, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=work, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=work, check=True)
    subprocess.run(
        ["git", "commit", "-q", "--allow-empty", "-m", "seed"], cwd=work, check=True
    )
    (pkg / "code.py").write_text("VALUE = 1\n")

    rules_dir = tmp_path / ".jig" / "rules" / "semgrep" / "boundaries"
    rules_dir.mkdir(parents=True)
    (rules_dir / "job-posting.yml").write_text(
        yaml.safe_dump(
            {
                "rules": [
                    build_deny_rule(
                        rule_id="boundary-job-posting-no-external-requests",
                        message="m",
                        package="requests",
                        package_dir="src/my_ats/job_posting/",
                    )
                ]
            }
        )
    )
    monkeypatch.setattr("jig.worktree.shutil.which", lambda _: None)  # semgrep "absent"

    orch = Orchestrator(project_path=tmp_path)
    threads = ThreadStore(tmp_path / "comments.jsonl")
    await threads.load()
    orch.threads = threads

    ok = await orch._auto_commit_worktree(work, "dev", "t1")
    assert ok is True
    entries = await threads.for_ticket("t1")
    assert any(
        e.kind == "system_event" and e.event_type == "boundary_check_degraded"
        for e in entries
    )


@pytest.mark.asyncio
async def test_auto_commit_boundary_violation_includes_detail(tmp_path):
    """A boundary violation during auto-commit posts the specific violations
    (not just the count) so the dev agent knows which imports to remove."""
    import shutil

    if shutil.which("semgrep") is None:
        pytest.skip("semgrep not installed")

    work = tmp_path / ".jig" / "worktrees" / "t1"
    pkg = work / "src" / "my_ats" / "job_posting"
    pkg.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=work, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=work, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=work, check=True)
    (pkg / "code.py").write_text("import requests\n")

    rules_dir = tmp_path / ".jig" / "rules" / "semgrep" / "boundaries"
    rules_dir.mkdir(parents=True)
    (rules_dir / "job-posting.yml").write_text(
        yaml.safe_dump(
            {
                "rules": [
                    build_deny_rule(
                        rule_id="boundary-job-posting-no-external-requests",
                        message="module 'job-posting' may not import 'requests'",
                        package="requests",
                        package_dir="src/my_ats/job_posting/",
                    )
                ]
            }
        )
    )

    orch = Orchestrator(project_path=tmp_path)
    threads = ThreadStore(tmp_path / "comments.jsonl")
    await threads.load()
    orch.threads = threads

    ok = await orch._auto_commit_worktree(work, "dev", "t1")
    assert ok is False  # violation blocks the auto-commit
    entries = await threads.for_ticket("t1")
    failed = [
        e
        for e in entries
        if e.kind == "system_event" and e.event_type == "auto_commit_failed"
    ]
    assert failed and "requests" in failed[0].content  # specific detail, not just count
