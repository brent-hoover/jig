"""Sanity tests for the v1 task corpus (plan step 9).

For every task under ``evals/prompt_style_eval/tasks/``, run its hidden tests
against its ``reference.py`` through the real sandbox and assert they pass.
This catches:

- Hidden tests that don't actually pass on a known-good solution (i.e. the
  task author wrote a buggy test).
- ``task.yaml`` metadata that doesn't load into the ``Task`` model.
- Drift between the reference solution and the test expectations.

It does NOT validate that the prompt files are equivalent to each other —
that's a code-review concern (see each task's ``README.md``).
"""

from pathlib import Path

import pytest
import yaml

from jig.evals.prompt_style_eval.classify import extract_files
from jig.evals.prompt_style_eval.loaders import task_fixture_paths
from jig.evals.prompt_style_eval.models import Task
from jig.evals.prompt_style_eval.sandbox import run_tests

_TASKS_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "jig"
    / "evals"
    / "prompt_style_eval"
    / "tasks"
)


def _discover_task_ids() -> list[str]:
    if not _TASKS_DIR.exists():
        return []
    return sorted(p.parent.name for p in _TASKS_DIR.glob("*/task.yaml"))


@pytest.mark.parametrize("task_id", _discover_task_ids())
async def test_reference_solution_passes_hidden_tests(task_id: str) -> None:
    task_dir = _TASKS_DIR / task_id
    task = Task(**yaml.safe_load((task_dir / "task.yaml").read_text(encoding="utf-8")))
    tests_dir = task_dir / "tests"

    # reference.md (preferred): markdown response with one or more named
    # code blocks, parsed by extract_files. Lets us exercise the full
    # extraction pipeline against a known-good response shape.
    # reference.py (legacy / single-file): plain Python source for the
    # task's entrypoint.
    reference_md = task_dir / "reference.md"
    reference_py = task_dir / "reference.py"
    if reference_md.exists():
        files = extract_files(
            reference_md.read_text(encoding="utf-8"),
            default_filename=task.entrypoint,
        )
        assert files, f"{task_id}/reference.md produced no extractable files"
    elif reference_py.exists():
        files = {task.entrypoint: reference_py.read_text(encoding="utf-8")}
    else:
        pytest.fail(f"{task_id}: neither reference.md nor reference.py present")

    fixtures = task_fixture_paths(task)
    result = await run_tests(files, task, tests_dir, fixtures=fixtures)
    assert result.passed, (
        f"reference solution for {task_id} failed its own hidden tests:\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )
    assert result.n_passed > 0, f"no tests ran for {task_id}"
    assert result.n_failed == 0


@pytest.mark.parametrize("task_id", _discover_task_ids())
def test_task_has_required_files(task_id: str) -> None:
    task_dir = _TASKS_DIR / task_id
    required = [
        "task.yaml",
        "description.md",
        "README.md",
        "tests",
        "prompts",
    ]
    missing = [name for name in required if not (task_dir / name).exists()]
    assert not missing, f"{task_id}: missing {missing}"
    # Either reference.md or reference.py must exist.
    has_reference = (task_dir / "reference.md").exists() or (task_dir / "reference.py").exists()
    assert has_reference, f"{task_id}: neither reference.md nor reference.py present"


@pytest.mark.parametrize("task_id", _discover_task_ids())
def test_task_has_at_least_two_prompt_variants(task_id: str) -> None:
    prompts = list((_TASKS_DIR / task_id / "prompts").glob("*.md"))
    assert len(prompts) >= 2, (
        f"{task_id}: needs at least two prompt variants for any comparison "
        f"(found {len(prompts)})"
    )
