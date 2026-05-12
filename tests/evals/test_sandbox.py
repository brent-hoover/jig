"""Tests for the sandbox (plan step 4).

Covers the four behaviors named in the plan:
- known-passing snippet → passed=True with counts
- known-failing snippet → passed=False with counts
- infinite-loop snippet → killed after timeout
- subprocess runs in the tmpdir (cwd containment)

Plus unit tests for ``parse_pytest_summary``.
"""

from pathlib import Path

import pytest

from evals.prompt_style_eval.models import Task
from evals.prompt_style_eval.sandbox import (
    default_pytest_command,
    parse_pytest_summary,
    run_tests,
)


def _make_task(timeout_s: int = 10) -> Task:
    return Task(
        id="t",
        version="v1",
        entrypoint="solution.py",
        test_command=default_pytest_command(),
        timeout_s=timeout_s,
    )


def _write_tests(tmp_path: Path, body: str) -> Path:
    tests_dir = tmp_path / "task_tests"
    tests_dir.mkdir()
    (tests_dir / "test_solution.py").write_text(body, encoding="utf-8")
    return tests_dir


# --- parse_pytest_summary --------------------------------------------------


def test_parse_summary_all_passed() -> None:
    out = "....\n3 passed in 0.05s\n"
    assert parse_pytest_summary(out) == (3, 0)


def test_parse_summary_mixed() -> None:
    out = "..F.\n1 failed, 3 passed in 0.12s\n"
    assert parse_pytest_summary(out) == (3, 1)


def test_parse_summary_errors_count_as_failed() -> None:
    out = "E\n1 error in 0.02s\n"
    assert parse_pytest_summary(out) == (0, 1)


def test_parse_summary_empty_returns_zeros() -> None:
    assert parse_pytest_summary("") == (0, 0)


def test_parse_summary_ignores_warnings_count() -> None:
    out = "..\n2 passed, 1 warning in 0.03s\n"
    assert parse_pytest_summary(out) == (2, 0)


# --- run_tests -------------------------------------------------------------


async def test_run_tests_passes_when_all_pass(tmp_path: Path) -> None:
    tests_dir = _write_tests(
        tmp_path,
        "from solution import greet\n\n"
        "def test_greet_returns_hi():\n"
        "    assert greet() == 'hi'\n",
    )
    code = "def greet() -> str:\n    return 'hi'\n"
    result = await run_tests(code, _make_task(), tests_dir)
    assert result.passed is True
    assert result.n_passed == 1
    assert result.n_failed == 0
    assert result.duration_s >= 0


async def test_run_tests_fails_when_tests_fail(tmp_path: Path) -> None:
    tests_dir = _write_tests(
        tmp_path,
        "from solution import greet\n\n"
        "def test_says_hi():\n"
        "    assert greet() == 'hi'\n"
        "def test_says_bye():\n"
        "    assert greet() == 'bye'\n",
    )
    code = "def greet() -> str:\n    return 'hi'\n"
    result = await run_tests(code, _make_task(), tests_dir)
    assert result.passed is False
    assert result.n_passed == 1
    assert result.n_failed == 1


async def test_run_tests_times_out_on_infinite_loop(tmp_path: Path) -> None:
    tests_dir = _write_tests(
        tmp_path,
        "from solution import loop\n\n"
        "def test_loop():\n"
        "    loop()\n",
    )
    code = "def loop() -> None:\n    while True:\n        pass\n"
    result = await run_tests(code, _make_task(timeout_s=2), tests_dir)
    assert result.passed is False
    assert "killed after 2s timeout" in result.stderr


async def test_subprocess_cwd_is_the_tmpdir(tmp_path: Path) -> None:
    """The test asserts ``os.getcwd()`` from the subprocess starts with the
    sandbox prefix — proves we're not running in the harness's cwd.
    """
    tests_dir = _write_tests(
        tmp_path,
        "import os\n\n"
        "def test_cwd_is_sandbox():\n"
        "    cwd = os.getcwd()\n"
        "    assert 'pse-sandbox-' in cwd, f'cwd was {cwd}'\n",
    )
    code = "# no functions needed\n"
    result = await run_tests(code, _make_task(), tests_dir)
    assert result.passed is True


async def test_runtime_error_in_solution_fails_loud(tmp_path: Path) -> None:
    tests_dir = _write_tests(
        tmp_path,
        "from solution import boom\n\n"
        "def test_boom():\n"
        "    boom()\n",
    )
    code = "def boom() -> None:\n    raise RuntimeError('expected')\n"
    result = await run_tests(code, _make_task(), tests_dir)
    assert result.passed is False
    assert result.n_failed == 1


@pytest.mark.parametrize("snippet", ["", "x = 1\n"])
async def test_import_error_is_a_failure_not_a_crash(
    tmp_path: Path, snippet: str
) -> None:
    """A solution with no expected symbol must produce a failed run, not raise."""
    tests_dir = _write_tests(
        tmp_path,
        "from solution import not_defined\n\n"
        "def test_anything():\n"
        "    assert not_defined() == 1\n",
    )
    result = await run_tests(snippet, _make_task(), tests_dir)
    assert result.passed is False
