"""Dev-gate module-boundary enforcement (step 5).

``_boundary_check`` runs orchestrator-side in ``commit_worktree``: it derives
the project root from the worktree layout, runs semgrep over the worktree
against the generated boundary rules, and raises ``BoundaryViolationError`` on
a match. semgrep-dependent cases skip when semgrep is absent; the
degradation/layout cases use a fake subprocess so they run anywhere.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

from jig.boundary_rules import build_deny_rule
from jig.worktree import BoundaryViolationError, _boundary_check

semgrep_required = pytest.mark.skipif(
    shutil.which("semgrep") is None, reason="semgrep not installed"
)


def _make_worktree(tmp_path: Path, *, code: str | None, rules: list[dict] | None) -> Path:
    """Build <project>/.jig/worktrees/t1/ with optional module code + project-
    root boundary rules. Returns the worktree path."""
    worktree = tmp_path / ".jig" / "worktrees" / "t1"
    pkg = worktree / "src" / "my_ats" / "job_posting"
    pkg.mkdir(parents=True)
    if code is not None:
        (pkg / "code.py").write_text(code)
    if rules is not None:
        rules_dir = tmp_path / ".jig" / "rules" / "semgrep" / "boundaries"
        rules_dir.mkdir(parents=True)
        (rules_dir / "job-posting.yml").write_text(yaml.safe_dump({"rules": rules}))
    return worktree


def _requests_rule() -> dict:
    return build_deny_rule(
        rule_id="boundary-job-posting-no-external-requests",
        message="module 'job-posting' may not import 'requests'",
        package="requests",
        package_dir="src/my_ats/job_posting/",
    )


@semgrep_required
async def test_boundary_check_raises_on_violation(tmp_path):
    worktree = _make_worktree(
        tmp_path, code="import requests\n", rules=[_requests_rule()]
    )
    with pytest.raises(BoundaryViolationError) as exc:
        await _boundary_check(worktree)
    assert exc.value.violations
    assert "requests" in exc.value.violations[0]


@semgrep_required
async def test_boundary_check_passes_compliant_code(tmp_path):
    worktree = _make_worktree(
        tmp_path, code="import httpx\n", rules=[_requests_rule()]
    )
    assert await _boundary_check(worktree) == []  # enforced clean, no warnings


async def test_boundary_check_noop_without_rules(tmp_path):
    worktree = _make_worktree(tmp_path, code="import requests\n", rules=None)
    assert await _boundary_check(worktree) == []  # no rules → no-op


async def test_boundary_check_warns_when_semgrep_missing(tmp_path, monkeypatch):
    worktree = _make_worktree(
        tmp_path, code="import requests\n", rules=[_requests_rule()]
    )
    monkeypatch.setattr("jig.worktree.shutil.which", lambda _: None)
    warnings = await _boundary_check(worktree)  # degrades to warn, no raise
    assert warnings and "semgrep" in warnings[0]


async def test_boundary_check_degrades_on_semgrep_error(tmp_path, monkeypatch):
    worktree = _make_worktree(
        tmp_path, code="import requests\n", rules=[_requests_rule()]
    )

    class _FakeProc:
        returncode = 2

        async def communicate(self):
            return b"", b"semgrep: bad rule"

    async def _fake_exec(*_a, **_k):
        return _FakeProc()

    monkeypatch.setattr("jig.worktree.shutil.which", lambda _: "/usr/bin/semgrep")
    monkeypatch.setattr(
        "jig.worktree.asyncio.create_subprocess_exec", _fake_exec
    )
    # exit >= 2 is a tool error → loud-degrade (warning returned), NOT a
    # BoundaryViolationError
    warnings = await _boundary_check(worktree)
    assert warnings and "abnormally" in warnings[0]


@pytest.mark.parametrize("rc, out", [(-9, b""), (0, b"not json"), (1, b"")])
async def test_boundary_check_degrades_on_abnormal_exit_or_garbage(
    tmp_path, monkeypatch, rc, out
):
    """A signal-killed semgrep (negative exit) or empty/garbage stdout must
    loud-degrade, never let a JSONDecodeError escape the gate."""
    worktree = _make_worktree(
        tmp_path, code="import requests\n", rules=[_requests_rule()]
    )

    class _FakeProc:
        returncode = rc

        async def communicate(self):
            return out, b"killed"

    async def _fake_exec(*_a, **_k):
        return _FakeProc()

    monkeypatch.setattr("jig.worktree.shutil.which", lambda _: "/usr/bin/semgrep")
    monkeypatch.setattr("jig.worktree.asyncio.create_subprocess_exec", _fake_exec)
    warnings = await _boundary_check(worktree)  # no exception escapes
    assert warnings and "NOT enforced" in warnings[0]


async def test_boundary_check_degrades_on_unexpected_result_shape(
    tmp_path, monkeypatch
):
    """A finding record missing the expected keys (e.g. a future semgrep shape)
    must loud-degrade, not let a KeyError escape the gate."""
    worktree = _make_worktree(
        tmp_path, code="import requests\n", rules=[_requests_rule()]
    )

    class _FakeProc:
        returncode = 0

        async def communicate(self):
            return json.dumps({"results": [{"extra": {}}]}).encode(), b""

    async def _fake_exec(*_a, **_k):
        return _FakeProc()

    monkeypatch.setattr("jig.worktree.shutil.which", lambda _: "/usr/bin/semgrep")
    monkeypatch.setattr("jig.worktree.asyncio.create_subprocess_exec", _fake_exec)
    warnings = await _boundary_check(worktree)
    assert warnings and "NOT enforced" in warnings[0]


async def test_boundary_check_noop_on_non_jig_worktree(tmp_path):
    """A worktree with no ancestor holding boundary rules (e.g. an ad-hoc /
    non-jig path) is a no-op — there are no rules, nothing to enforce."""
    bogus = tmp_path / "not" / "a" / "worktree"
    bogus.mkdir(parents=True)
    await _boundary_check(bogus)  # no raise
