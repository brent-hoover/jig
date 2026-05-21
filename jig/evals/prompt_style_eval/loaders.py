"""On-disk loaders for tasks, prompts, and rubrics.

Each helper returns a fully-validated pydantic model. Filesystem layout is
fixed: tasks live under ``tasks/<id>/``, prompts under ``tasks/<id>/prompts/
<prompt_id>.md``, rubrics under ``rubric/<version>.yaml`` (all relative to
the package root).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import yaml

from jig.evals.prompt_style_eval.models import Prompt, Rubric, Task

_PACKAGE_ROOT = Path(__file__).resolve().parent
TASKS_DIR = _PACKAGE_ROOT / "tasks"
RUBRIC_DIR = _PACKAGE_ROOT / "rubric"


def list_task_ids() -> list[str]:
    if not TASKS_DIR.exists():
        return []
    return sorted(p.parent.name for p in TASKS_DIR.glob("*/task.yaml"))


def load_task(task_id: str) -> Task:
    task_yaml = TASKS_DIR / task_id / "task.yaml"
    if not task_yaml.exists():
        raise FileNotFoundError(f"no such task: {task_id} (looked at {task_yaml})")
    return Task(**yaml.safe_load(task_yaml.read_text(encoding="utf-8")))


def task_tests_dir(task_id: str) -> Path:
    return TASKS_DIR / task_id / "tests"


def task_fixture_paths(task: Task) -> list[Path]:
    """Resolve ``task.fixtures`` (strings, relative to the task dir) to
    absolute paths for the sandbox to copy."""
    task_dir = TASKS_DIR / task.id
    return [task_dir / name for name in task.fixtures]


def list_prompt_ids(task_id: str) -> list[str]:
    prompts_dir = TASKS_DIR / task_id / "prompts"
    if not prompts_dir.exists():
        return []
    return sorted(p.stem for p in prompts_dir.glob("*.md"))


def load_prompt(task_id: str, prompt_id: str) -> Prompt:
    path = TASKS_DIR / task_id / "prompts" / f"{prompt_id}.md"
    if not path.exists():
        raise FileNotFoundError(f"no such prompt: {task_id}/{prompt_id} (looked at {path})")
    text = path.read_text(encoding="utf-8")
    content_hash = "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
    return Prompt(
        task_id=task_id,
        prompt_id=prompt_id,
        text=text,
        content_hash=content_hash,
    )


def load_rubric(version: str) -> Rubric:
    path = RUBRIC_DIR / f"{version}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"no such rubric: {version} (looked at {path})")
    return Rubric(**yaml.safe_load(path.read_text(encoding="utf-8")))
