"""Tests for jig.code_metrics — the deterministic code-quality signal.

Real radon, ruff, and git are used (radon + ruff are project deps; git is
always present in the dev/sandbox environment). No mocks: the whole point of
this module is to react to real tool output, so the tests do too.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from jig.code_metrics import (
    CC_FLAG_THRESHOLD,
    ChangeMetrics,
    cc_with_location,
    compute_change_metrics,
    count_loc,
    max_cyclomatic,
)

# --- moved primitives still work when imported from the new home -----------


def test_count_loc_ignores_blank_and_comment_lines() -> None:
    assert count_loc("") == 0
    assert count_loc("# just a comment\n") == 0
    assert count_loc("x = 1\n\n# c\ny = 2\n") == 2


def test_max_cyclomatic_zero_without_functions() -> None:
    assert max_cyclomatic("x = 1\n") == 0


def test_max_cyclomatic_counts_branches() -> None:
    code = "def f(x):\n    if x:\n        return 1\n    return 0\n"
    assert max_cyclomatic(code) == 2


def test_max_cyclomatic_survives_syntax_error() -> None:
    assert max_cyclomatic("def f(:\n") == 0


# --- cc_with_location ------------------------------------------------------


def test_cc_with_location_returns_offending_function_name() -> None:
    code = (
        "def simple():\n"
        "    return 1\n"
        "\n"
        "def branchy(x):\n"
        "    if x > 0:\n"
        "        return 1\n"
        "    elif x < 0:\n"
        "        return -1\n"
        "    else:\n"
        "        return 0\n"
    )
    cc, name = cc_with_location(code)
    assert cc == 3
    assert name == "branchy"


def test_cc_with_location_none_without_functions() -> None:
    cc, name = cc_with_location("x = 1\n")
    assert cc == 0
    assert name is None


# --- ChangeMetrics.flagged threshold ---------------------------------------


def test_flag_threshold_is_ten() -> None:
    assert CC_FLAG_THRESHOLD == 10


@pytest.mark.parametrize(("max_cc", "expected"), [(10, False), (11, True)])
def test_changemetrics_flagged_derives_from_threshold(
    max_cc: int, expected: bool
) -> None:
    m = ChangeMetrics(
        max_cc=max_cc,
        max_cc_location=None,
        ruff_findings=0,
        loc_delta=0,
    )
    assert m.flagged is expected


# --- compute_change_metrics over a real git worktree -----------------------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        env={
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
            "HOME": str(repo),
            "PATH": __import__("os").environ.get("PATH", ""),
        },
    )


@pytest.fixture
def base_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    (repo / "pyproject.toml").write_text("[project]\nname='t'\nversion='0'\n")
    (repo / "base.py").write_text("def base():\n    return 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "base")
    return repo


def _make_high_cc(n_branches: int) -> str:
    """A `grade` function with `n_branches` decision points → CC == n_branches + 1."""
    lines = ["def grade(score):", "    if score >= 0:", "        return 0"]
    for i in range(1, n_branches):
        lines += [f"    elif score >= {i}:", f"        return {i}"]
    lines += ["    else:", "        return -1"]
    return "\n".join(lines) + "\n"


# 12 decision points → CC 13, comfortably above CC_FLAG_THRESHOLD (10).
HIGH_CC_FUNC = _make_high_cc(12)


async def test_committed_change_is_measured(base_repo: Path) -> None:
    _git(base_repo, "checkout", "-b", "feature")
    (base_repo / "feature.py").write_text(HIGH_CC_FUNC)
    _git(base_repo, "add", "-A")
    _git(base_repo, "commit", "-m", "add high-cc")

    m = await compute_change_metrics(base_repo, base_ref="main")

    assert m.max_cc >= 11
    assert m.max_cc_location is not None
    assert "feature.py" in m.max_cc_location
    assert "grade" in m.max_cc_location
    assert m.flagged is True
    assert m.loc_delta > 0
    assert m.ruff_findings == 0


async def test_uncommitted_working_tree_change_is_measured(base_repo: Path) -> None:
    # commit_worktree calls compute_change_metrics BEFORE `git add -A`,
    # so a dirty working tree must still be picked up.
    (base_repo / "feature.py").write_text(HIGH_CC_FUNC)

    m = await compute_change_metrics(base_repo, base_ref="main")

    assert m.max_cc >= 11
    assert m.flagged is True


async def test_syntax_error_file_does_not_raise(base_repo: Path) -> None:
    _git(base_repo, "checkout", "-b", "broken")
    (base_repo / "broken.py").write_text("def f(:\n    pass\n")
    _git(base_repo, "add", "-A")
    _git(base_repo, "commit", "-m", "broken")

    m = await compute_change_metrics(base_repo, base_ref="main")

    assert m.max_cc == 0
    assert m.flagged is False


async def test_no_python_changes_yields_zeros(base_repo: Path) -> None:
    _git(base_repo, "checkout", "-b", "docs")
    (base_repo / "README.md").write_text("# hi\n")
    _git(base_repo, "add", "-A")
    _git(base_repo, "commit", "-m", "docs only")

    m = await compute_change_metrics(base_repo, base_ref="main")

    assert m.max_cc == 0
    assert m.loc_delta == 0
    assert m.ruff_findings == 0
    assert m.flagged is False


async def test_unresolvable_base_ref_degrades_gracefully(base_repo: Path) -> None:
    (base_repo / "feature.py").write_text(HIGH_CC_FUNC)

    # No raise even though the base ref doesn't exist; falls back to a
    # working-tree comparison and still reports something sane.
    m = await compute_change_metrics(base_repo, base_ref="does-not-exist")

    assert isinstance(m, ChangeMetrics)
    assert m.max_cc >= 0


async def test_ruff_findings_counted(base_repo: Path) -> None:
    _git(base_repo, "checkout", "-b", "lint")
    # F401: unused import — on by default in ruff.
    (base_repo / "feature.py").write_text("import os\n\n\ndef f():\n    return 1\n")
    _git(base_repo, "add", "-A")
    _git(base_repo, "commit", "-m", "unused import")

    m = await compute_change_metrics(base_repo, base_ref="main")

    assert m.ruff_findings >= 1


async def test_change_metrics_includes_taxonomy_hits(base_repo: Path) -> None:
    # B006 (mutable default arg) maps to TAX-LANG-001 in the taxonomy.
    (base_repo / "feature.py").write_text("def g(x=[]):\n    return x\n")

    m = await compute_change_metrics(base_repo, base_ref="main")

    assert any(h.id == "TAX-LANG-001" for h in m.taxonomy_hits), m.taxonomy_hits


async def test_change_metrics_no_taxonomy_hits_on_clean_change(base_repo: Path) -> None:
    (base_repo / "feature.py").write_text("def g(x: int) -> int:\n    return x + 1\n")

    m = await compute_change_metrics(base_repo, base_ref="main")

    assert m.taxonomy_hits == ()
