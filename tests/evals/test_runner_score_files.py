from jig.evals.prompt_style_eval.models import Task
from jig.evals.prompt_style_eval.runner import (
    _judge_score_files,
    _judge_source,
    _score_files,
)


def test_judge_score_files_includes_missing_configured_files() -> None:
    task = Task(
        id="review_fix_ladder",
        version="v3",
        entrypoint="cart_totals.py",
        test_command=["python", "-m", "pytest"],
        timeout_s=30,
        score_files=["cart_totals.py", "scheduler.py"],
    )

    scored = _judge_score_files({"cart_totals.py": "x = 1\n"}, task)
    judge_source = _judge_source(scored)

    assert set(scored) == {"cart_totals.py", "scheduler.py"}
    assert "Missing required score file: scheduler.py" in scored["scheduler.py"]
    assert "# File: scheduler.py" in judge_source
    assert "Missing required score file: scheduler.py" in judge_source


def test_score_files_uses_only_actual_candidate_files() -> None:
    task = Task(
        id="review_fix_ladder",
        version="v3",
        entrypoint="cart_totals.py",
        test_command=["python", "-m", "pytest"],
        timeout_s=30,
        score_files=["cart_totals.py", "scheduler.py"],
    )

    scored = _score_files({"cart_totals.py": "x = 1\n"}, task)

    assert scored == {"cart_totals.py": "x = 1\n"}
    assert "scheduler.py" not in scored


def test_score_files_falls_back_to_actual_files_when_names_do_not_match() -> None:
    task = Task(
        id="review_fix_ladder",
        version="v3",
        entrypoint="cart_totals.py",
        test_command=["python", "-m", "pytest"],
        timeout_s=30,
        score_files=["cart_totals.py", "scheduler.py"],
    )

    scored = _score_files({"solution.py": "x = 1\n"}, task)

    assert scored == {"solution.py": "x = 1\n"}


def test_score_files_keeps_entrypoint_fallback_for_single_file_tasks() -> None:
    task = Task(
        id="todo_cli",
        version="v1",
        entrypoint="todo.py",
        test_command=["python", "-m", "pytest"],
        timeout_s=30,
    )

    scored = _score_files({"todo.py": "x = 1\n", "extra.py": "y = 2\n"}, task)

    assert scored == {"todo.py": "x = 1\n"}
