"""Tests for static-metrics (plan step 7).

Real ruff and radon are used; both are project dependencies. The tests
cover the three plan checkpoints: clean snippet → no findings; bare except
→ flagged; LoC matches manual count. Plus the edge cases (empty, comments
only, syntax error, no functions) where ``count_loc`` and ``max_cyclomatic``
could silently misbehave.
"""

import pytest

from jig.evals.prompt_style_eval.metrics import (
    compute,
    compute_files,
    count_loc,
    max_cyclomatic,
    run_ruff,
)


# --- count_loc -------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("", 0),
        ("\n\n\n", 0),
        ("# nothing but a comment\n", 0),
        ("x = 1\n", 1),
        ("x = 1\ny = 2\n", 2),
        ("x = 1\n\ny = 2\n", 2),
        ("# leading comment\nx = 1\n", 1),
        ("x = 1  # trailing comment\n", 1),
    ],
)
def test_count_loc(code: str, expected: int) -> None:
    assert count_loc(code) == expected


# --- max_cyclomatic --------------------------------------------------------


def test_cyclomatic_zero_for_empty_code() -> None:
    assert max_cyclomatic("") == 0


def test_cyclomatic_zero_for_no_functions() -> None:
    assert max_cyclomatic("x = 1\ny = 2\n") == 0


def test_cyclomatic_for_simple_function() -> None:
    code = "def f():\n    return 1\n"
    assert max_cyclomatic(code) == 1


def test_cyclomatic_increases_with_branching() -> None:
    code = "def f(x):\n    if x:\n        return 1\n    return 0\n"
    assert max_cyclomatic(code) == 2


def test_cyclomatic_picks_max_across_functions() -> None:
    code = (
        "def simple():\n"
        "    return 1\n"
        "def branchy(x):\n"
        "    if x > 0:\n"
        "        return 1\n"
        "    elif x < 0:\n"
        "        return -1\n"
        "    return 0\n"
    )
    assert max_cyclomatic(code) == 3


def test_cyclomatic_returns_zero_on_syntax_error() -> None:
    assert max_cyclomatic("def f(:\n    pass\n") == 0


# --- ruff via subprocess ---------------------------------------------------


async def test_run_ruff_clean_snippet_has_no_findings() -> None:
    code = "x: int = 1\n"
    total, breakdown = await run_ruff(code)
    assert total == 0
    assert breakdown == {}


async def test_run_ruff_flags_bare_except() -> None:
    code = (
        "def boom() -> None:\n"
        "    try:\n"
        "        risky()\n"
        "    except:\n"  # noqa: E722  (we WANT ruff to flag this)
        "        pass\n"
        "def risky() -> None:\n"
        "    raise RuntimeError\n"
    )
    total, breakdown = await run_ruff(code)
    assert total >= 1
    assert "E722" in breakdown


# --- compute() integration -------------------------------------------------


async def test_compute_returns_static_metrics() -> None:
    code = (
        "def greet(name: str) -> str:\n"
        "    if not name:\n"
        "        return 'hello'\n"
        "    return f'hello, {name}'\n"
    )
    metrics = await compute(code)
    assert metrics.loc == 4
    assert metrics.cyclomatic_max == 2
    assert metrics.ruff_findings == 0
    assert metrics.ruff_breakdown == {}


async def test_compute_surfaces_bare_except_in_breakdown() -> None:
    code = (
        "def bad() -> None:\n"
        "    try:\n"
        "        pass\n"
        "    except:\n"  # noqa: E722
        "        pass\n"
    )
    metrics = await compute(code)
    assert metrics.ruff_findings >= 1
    assert "E722" in metrics.ruff_breakdown


async def test_compute_files_aggregates_metrics_per_file() -> None:
    metrics = await compute_files(
        {
            "a.py": "def simple() -> int:\n    return 1\n",
            "b.py": (
                "def branchy(value: int) -> int:\n"
                "    if value > 0:\n"
                "        return 1\n"
                "    return 0\n"
            ),
        }
    )

    assert metrics.loc == 6
    assert metrics.cyclomatic_max == 2
    assert metrics.ruff_findings == 0
