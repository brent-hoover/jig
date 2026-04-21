"""Tests for ScriptedRunner (Phase 5 Task A).

Covers scripted-check execution: verdict mapping, output capture,
timeout handling, working_dir resolution, commit_sha pinning, and
the type dispatch between scripted and agent checks.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from jig.check_runner import ScriptedRunner, severity_for
from jig.checks import (
    BlackBoxAgentCheck,
    CheckCatalog,
    CheckSeverity,
    ScriptedCheck,
)
from jig.store.check_results import CheckResultsStore


def _catalog(**checks) -> CheckCatalog:
    return CheckCatalog.model_validate(checks)


async def _store(tmp_path: Path) -> CheckResultsStore:
    store = CheckResultsStore(tmp_path / "results.jsonl")
    await store.load()
    return store


def _worktree(tmp_path: Path, *, git: bool = False) -> Path:
    root = tmp_path / "wt"
    root.mkdir()
    if git:
        subprocess.run(
            ["git", "init", "-q"], cwd=root, check=True
        )
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t",
             "commit", "--allow-empty", "-qm", "init"],
            cwd=root, check=True,
        )
    return root


class TestVerdictMapping:
    async def test_pass_on_exit_zero(self, tmp_path: Path) -> None:
        cat = _catalog(ok=ScriptedCheck(type="scripted", command="true"))
        store = await _store(tmp_path)
        runner = ScriptedRunner(
            catalog=cat, results=store, worktree_path=_worktree(tmp_path)
        )
        result = await runner.run_check(
            ticket_id="t1", phase="dev", check_name="ok"
        )
        assert result.verdict == "pass"
        assert result.check_type == "scripted"
        assert result.severity == CheckSeverity.REQUIRED

    async def test_fail_on_exit_nonzero(self, tmp_path: Path) -> None:
        cat = _catalog(
            bad=ScriptedCheck(type="scripted", command="false")
        )
        store = await _store(tmp_path)
        runner = ScriptedRunner(
            catalog=cat, results=store, worktree_path=_worktree(tmp_path)
        )
        result = await runner.run_check(
            ticket_id="t1", phase="dev", check_name="bad"
        )
        assert result.verdict == "fail"

    async def test_timeout_kills_and_records(self, tmp_path: Path) -> None:
        cat = _catalog(
            slow=ScriptedCheck(
                type="scripted", command="sleep 5", timeout_s=1
            )
        )
        store = await _store(tmp_path)
        runner = ScriptedRunner(
            catalog=cat, results=store, worktree_path=_worktree(tmp_path)
        )
        result = await runner.run_check(
            ticket_id="t1", phase="dev", check_name="slow"
        )
        assert result.verdict == "timeout"

    async def test_error_on_bad_cwd(self, tmp_path: Path) -> None:
        cat = _catalog(
            x=ScriptedCheck(
                type="scripted", command="echo hi", working_dir="missing"
            )
        )
        store = await _store(tmp_path)
        runner = ScriptedRunner(
            catalog=cat, results=store, worktree_path=_worktree(tmp_path)
        )
        result = await runner.run_check(
            ticket_id="t1", phase="dev", check_name="x"
        )
        # Bad cwd either surfaces as a spawn error or as a nonzero
        # exit from /bin/sh; either is an acceptable record.
        assert result.verdict in ("error", "fail")


class TestOutputCapture:
    async def test_captures_stdout(self, tmp_path: Path) -> None:
        cat = _catalog(
            echo=ScriptedCheck(type="scripted", command="echo hello-jig")
        )
        store = await _store(tmp_path)
        runner = ScriptedRunner(
            catalog=cat, results=store, worktree_path=_worktree(tmp_path)
        )
        result = await runner.run_check(
            ticket_id="t1", phase="dev", check_name="echo"
        )
        assert "hello-jig" in result.output

    async def test_captures_stderr_combined(self, tmp_path: Path) -> None:
        cat = _catalog(
            err=ScriptedCheck(
                type="scripted",
                command="echo normal; echo boom 1>&2; exit 1",
            )
        )
        store = await _store(tmp_path)
        runner = ScriptedRunner(
            catalog=cat, results=store, worktree_path=_worktree(tmp_path)
        )
        result = await runner.run_check(
            ticket_id="t1", phase="dev", check_name="err"
        )
        assert result.verdict == "fail"
        assert "normal" in result.output
        assert "boom" in result.output


class TestCommitSha:
    async def test_populated_in_git_worktree(self, tmp_path: Path) -> None:
        cat = _catalog(ok=ScriptedCheck(type="scripted", command="true"))
        store = await _store(tmp_path)
        runner = ScriptedRunner(
            catalog=cat,
            results=store,
            worktree_path=_worktree(tmp_path, git=True),
        )
        result = await runner.run_check(
            ticket_id="t1", phase="dev", check_name="ok"
        )
        assert len(result.commit_sha) == 40

    async def test_empty_for_non_git_dir(self, tmp_path: Path) -> None:
        cat = _catalog(ok=ScriptedCheck(type="scripted", command="true"))
        store = await _store(tmp_path)
        runner = ScriptedRunner(
            catalog=cat, results=store, worktree_path=_worktree(tmp_path)
        )
        result = await runner.run_check(
            ticket_id="t1", phase="dev", check_name="ok"
        )
        assert result.commit_sha == ""


class TestWorkingDir:
    async def test_relative_to_worktree(self, tmp_path: Path) -> None:
        wt = _worktree(tmp_path)
        (wt / "sub").mkdir()
        (wt / "sub" / "marker").write_text("here")
        cat = _catalog(
            ls=ScriptedCheck(
                type="scripted", command="ls", working_dir="sub"
            )
        )
        store = await _store(tmp_path)
        runner = ScriptedRunner(
            catalog=cat, results=store, worktree_path=wt
        )
        result = await runner.run_check(
            ticket_id="t1", phase="dev", check_name="ls"
        )
        assert result.verdict == "pass"
        assert "marker" in result.output


class TestDispatch:
    async def test_unknown_check_raises(self, tmp_path: Path) -> None:
        store = await _store(tmp_path)
        runner = ScriptedRunner(
            catalog=_catalog(),
            results=store,
            worktree_path=_worktree(tmp_path),
        )
        with pytest.raises(KeyError):
            await runner.run_check(
                ticket_id="t1", phase="dev", check_name="nope"
            )

    async def test_non_scripted_check_raises_type_error(
        self, tmp_path: Path
    ) -> None:
        cat = _catalog(
            agent=BlackBoxAgentCheck(
                type="black_box_agent",
                template="x",
            )
        )
        store = await _store(tmp_path)
        runner = ScriptedRunner(
            catalog=cat, results=store, worktree_path=_worktree(tmp_path)
        )
        with pytest.raises(TypeError):
            await runner.run_check(
                ticket_id="t1", phase="dev", check_name="agent"
            )


class TestPersistence:
    async def test_result_is_stored(self, tmp_path: Path) -> None:
        cat = _catalog(ok=ScriptedCheck(type="scripted", command="true"))
        store = await _store(tmp_path)
        runner = ScriptedRunner(
            catalog=cat, results=store, worktree_path=_worktree(tmp_path)
        )
        await runner.run_check(
            ticket_id="t1", phase="dev", check_name="ok"
        )
        persisted = await store.for_phase("t1", "dev")
        assert len(persisted) == 1
        assert persisted[0].check_name == "ok"
        assert persisted[0].verdict == "pass"


class TestRunForPhase:
    async def test_runs_only_scripted_checks(
        self, tmp_path: Path
    ) -> None:
        cat = _catalog(
            unit=ScriptedCheck(type="scripted", command="true"),
            lint=ScriptedCheck(type="scripted", command="false"),
            review=BlackBoxAgentCheck(
                type="black_box_agent", template="x"
            ),
        )
        store = await _store(tmp_path)
        runner = ScriptedRunner(
            catalog=cat, results=store, worktree_path=_worktree(tmp_path)
        )
        results = await runner.run_for_phase(
            ticket_id="t1",
            phase="dev",
            check_names=["unit", "review", "lint"],
        )
        assert [r.check_name for r in results] == ["unit", "lint"]
        assert {r.verdict for r in results} == {"pass", "fail"}

    async def test_unknown_check_raises(self, tmp_path: Path) -> None:
        store = await _store(tmp_path)
        runner = ScriptedRunner(
            catalog=_catalog(),
            results=store,
            worktree_path=_worktree(tmp_path),
        )
        with pytest.raises(KeyError):
            await runner.run_for_phase(
                ticket_id="t1", phase="dev", check_names=["missing"]
            )


class TestSeverityHelper:
    def test_returns_severity_when_present(self) -> None:
        cat = _catalog(
            x=ScriptedCheck(
                type="scripted",
                command="true",
                severity=CheckSeverity.WARNING,
            )
        )
        assert severity_for(cat, "x") == CheckSeverity.WARNING

    def test_returns_none_for_missing(self) -> None:
        assert severity_for(_catalog(), "nope") is None
