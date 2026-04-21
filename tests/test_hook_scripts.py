"""End-to-end tests for the capability enforcement hook scripts
(Phase 5 Task G, doc 16).

Each script (``check-bash``, ``check-write``, ``check-path``) is
invoked exactly how Claude Code invokes it in the sandbox: as an
executable with the hook payload on stdin and the rules file at a
configurable path. We cannot write to ``/jig/policy/`` on a dev
machine, and coupling tests to a root-owned fixed path would make
this suite unrunnable locally — so the tests redirect the rules
lookup to a tmp file instead.

Redirection mechanism: each hook script calls
``_hooklib.load_rules()`` with no argument, which reads
``DEFAULT_RULES_PATH`` (``/jig/policy/rules.json``). The production
script takes no env-var override — it trusts its compiled-in
constant. To point it at a tmp file without touching the production
source, :func:`_run_hook` spawns a subprocess that executes a small
Python shim: the shim imports ``_hooklib``, reassigns
``_hooklib.DEFAULT_RULES_PATH`` to the test path, then uses
``runpy.run_path`` to execute the hook script under
``__name__ == '__main__'``. Because the hook script imports the
same ``_hooklib`` module, it sees the overridden default. This
keeps the script itself in lockstep with how Claude Code invokes
it in production (no test-only conditionals)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from jig.bin import hook_bin_dir

_BIN = hook_bin_dir()


def _run_hook(
    script: str,
    payload: dict,
    rules_path: Path,
) -> subprocess.CompletedProcess:
    """Invoke one of the hook scripts with ``payload`` on stdin and
    ``rules_path`` bound in as the rules location via env var.

    We wrap the real script with a tiny shim so the rules-file path is
    configurable without touching the production scripts. The shim
    overrides ``_hooklib.DEFAULT_RULES_PATH`` before dispatching."""

    shim = f"""
import runpy, sys
sys.path.insert(0, {str(_BIN)!r})
import _hooklib
_hooklib.DEFAULT_RULES_PATH = {str(rules_path)!r}
runpy.run_path({str(_BIN / script)!r}, run_name='__main__')
"""
    return subprocess.run(
        [sys.executable, "-c", shim],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=10,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )


@pytest.fixture
def rules_file(tmp_path: Path) -> Path:
    """A fresh rules.json path the test populates before invoking the hook."""
    return tmp_path / "rules.json"


def _write_rules(path: Path, rules: dict) -> None:
    path.write_text(json.dumps(rules))


# ---- check-bash ---------------------------------------------------------


class TestCheckBash:
    def test_allows_when_no_pattern_matches(self, rules_file: Path) -> None:
        _write_rules(
            rules_file,
            {"bash": {"deny_patterns": [r"rm\s+-rf", r"git\s+push\s+--force"]}},
        )
        result = _run_hook(
            "check-bash",
            {"tool_name": "Bash", "tool_input": {"command": "ls -la"}},
            rules_file,
        )
        assert result.returncode == 0, result.stderr

    def test_denies_on_matching_pattern(self, rules_file: Path) -> None:
        _write_rules(rules_file, {"bash": {"deny_patterns": [r"rm\s+-rf"]}})
        result = _run_hook(
            "check-bash",
            {"tool_name": "Bash", "tool_input": {"command": "rm -rf build/"}},
            rules_file,
        )
        assert result.returncode == 2
        assert "blocked by policy" in result.stderr
        assert "rm" in result.stderr

    def test_empty_deny_list_allows(self, rules_file: Path) -> None:
        _write_rules(rules_file, {"bash": {"deny_patterns": []}})
        result = _run_hook(
            "check-bash",
            {"tool_name": "Bash", "tool_input": {"command": "rm -rf build/"}},
            rules_file,
        )
        # With nothing declared to deny, the hook allows — this state
        # is unreachable in production (compiler only registers the
        # hook when deny_patterns is non-empty) but the script must
        # still behave correctly if invoked.
        assert result.returncode == 0

    def test_non_bash_call_passed_through(self, rules_file: Path) -> None:
        _write_rules(rules_file, {"bash": {"deny_patterns": [r"rm\s+-rf"]}})
        # Claude Code could theoretically route a Write call here if
        # matchers widen. We allow it — other hooks gate Write.
        result = _run_hook(
            "check-bash",
            {
                "tool_name": "Write",
                "tool_input": {"file_path": "/workspace/foo.py", "content": ""},
            },
            rules_file,
        )
        assert result.returncode == 0

    def test_missing_rules_file_denies(self, tmp_path: Path) -> None:
        # Fail-closed: if rules.json is missing the hook denies. This
        # matters because a sandbox misconfiguration should not let
        # tool calls through unchecked.
        result = _run_hook(
            "check-bash",
            {"tool_name": "Bash", "tool_input": {"command": "ls"}},
            tmp_path / "does-not-exist.json",
        )
        assert result.returncode == 2
        assert "blocked by policy" in result.stderr

    def test_invalid_regex_in_rules_denies(self, rules_file: Path) -> None:
        # A bad regex should have been caught by catalog validation,
        # but if it slips through (hand-edited rules.json) we deny
        # rather than silently allow.
        _write_rules(rules_file, {"bash": {"deny_patterns": ["["]}})
        result = _run_hook(
            "check-bash",
            {"tool_name": "Bash", "tool_input": {"command": "ls"}},
            rules_file,
        )
        assert result.returncode == 2
        assert "not a valid regex" in result.stderr


# ---- check-write --------------------------------------------------------


class TestCheckWrite:
    def test_allows_path_in_writable_list(self, rules_file: Path) -> None:
        _write_rules(
            rules_file,
            {"paths": {"writable": ["ticket://worktree/src/**"], "denied": []}},
        )
        result = _run_hook(
            "check-write",
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": "/workspace/src/foo.py",
                    "content": "",
                },
            },
            rules_file,
        )
        assert result.returncode == 0, result.stderr

    def test_denies_path_outside_writable_list(self, rules_file: Path) -> None:
        _write_rules(
            rules_file,
            {"paths": {"writable": ["ticket://worktree/src/**"], "denied": []}},
        )
        result = _run_hook(
            "check-write",
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": "/workspace/.jig/spec/foo.yaml",
                    "content": "",
                },
            },
            rules_file,
        )
        assert result.returncode == 2
        assert "not in the role's writable paths" in result.stderr

    def test_denied_wins_over_writable(self, rules_file: Path) -> None:
        # ``paths.denied`` takes priority over ``paths.writable`` even
        # when both match — deny-wins is the doc 16 contract.
        _write_rules(
            rules_file,
            {
                "paths": {
                    "writable": ["ticket://worktree/**"],
                    "denied": ["ticket://worktree/.jig/spec/**"],
                }
            },
        )
        result = _run_hook(
            "check-write",
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": "/workspace/.jig/spec/project.yaml",
                    "content": "",
                },
            },
            rules_file,
        )
        assert result.returncode == 2
        assert "denied path" in result.stderr

    def test_empty_writable_allows_anything_not_denied(self, rules_file: Path) -> None:
        # Empty writable list = "rely on bwrap bounds"; only explicit
        # denies apply.
        _write_rules(
            rules_file,
            {"paths": {"writable": [], "denied": ["ticket://worktree/secrets/**"]}},
        )
        result = _run_hook(
            "check-write",
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": "/workspace/docs/readme.md",
                    "content": "",
                },
            },
            rules_file,
        )
        assert result.returncode == 0

    def test_relative_path_resolved_against_workspace(self, rules_file: Path) -> None:
        _write_rules(
            rules_file,
            {"paths": {"writable": ["ticket://worktree/src/**"], "denied": []}},
        )
        result = _run_hook(
            "check-write",
            {
                "tool_name": "Edit",
                "tool_input": {"file_path": "src/foo.py"},
            },
            rules_file,
        )
        assert result.returncode == 0

    def test_notebook_edit_uses_notebook_path(self, rules_file: Path) -> None:
        _write_rules(
            rules_file,
            {"paths": {"writable": ["ticket://worktree/notebooks/**"], "denied": []}},
        )
        result = _run_hook(
            "check-write",
            {
                "tool_name": "NotebookEdit",
                "tool_input": {
                    "notebook_path": "/workspace/notebooks/a.ipynb",
                    "new_source": "print(1)",
                },
            },
            rules_file,
        )
        assert result.returncode == 0


# ---- check-path ---------------------------------------------------------


class TestCheckPath:
    def test_allows_read_in_readable_list(self, rules_file: Path) -> None:
        _write_rules(
            rules_file,
            {"paths": {"readable": ["ticket://worktree/**"], "denied": []}},
        )
        result = _run_hook(
            "check-path",
            {
                "tool_name": "Read",
                "tool_input": {"file_path": "/workspace/src/foo.py"},
            },
            rules_file,
        )
        assert result.returncode == 0

    def test_denies_read_outside_readable_list(self, rules_file: Path) -> None:
        _write_rules(
            rules_file,
            {"paths": {"readable": ["ticket://worktree/src/**"], "denied": []}},
        )
        result = _run_hook(
            "check-path",
            {
                "tool_name": "Read",
                "tool_input": {"file_path": "/etc/passwd"},
            },
            rules_file,
        )
        assert result.returncode == 2
        assert "not in the role's readable paths" in result.stderr

    def test_denied_wins_over_readable(self, rules_file: Path) -> None:
        _write_rules(
            rules_file,
            {
                "paths": {
                    "readable": ["ticket://worktree/**"],
                    "denied": ["ticket://worktree/.jig/secrets/**"],
                }
            },
        )
        result = _run_hook(
            "check-path",
            {
                "tool_name": "Read",
                "tool_input": {"file_path": "/workspace/.jig/secrets/key"},
            },
            rules_file,
        )
        assert result.returncode == 2
        assert "denied path" in result.stderr

    def test_glob_without_path_uses_workspace(self, rules_file: Path) -> None:
        # Glob/Grep with no ``path`` defaults to cwd (/workspace). The
        # hook must enforce the rule even when the field is absent.
        _write_rules(
            rules_file,
            {"paths": {"readable": [], "denied": ["/workspace/**"]}},
        )
        result = _run_hook(
            "check-path",
            {"tool_name": "Glob", "tool_input": {"pattern": "**/*.py"}},
            rules_file,
        )
        assert result.returncode == 2

    def test_non_read_tool_passed_through(self, rules_file: Path) -> None:
        _write_rules(
            rules_file,
            {"paths": {"readable": ["ticket://worktree/src/**"], "denied": []}},
        )
        result = _run_hook(
            "check-path",
            {
                "tool_name": "Bash",
                "tool_input": {"command": "ls"},
            },
            rules_file,
        )
        assert result.returncode == 0
