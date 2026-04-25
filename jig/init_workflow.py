"""CLI coordinator for ``jig init <name>``.

Dispatches between fresh-init and resume, drives the PO / spec-gen /
SA conversation loops, and finalizes scaffold. v1: stub creation only —
PO/spec-gen/SA are wired in later tasks.
"""
from __future__ import annotations

import shutil
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

import click
import yaml

from jig.atomic import atomic_write_text


class DirState(str, Enum):
    FRESH = "fresh"
    IN_PROGRESS = "in_progress"
    ALREADY_DONE = "already_done"
    BROKEN = "broken"


def classify_directory(path: Path) -> DirState:
    """Inspect ``path`` and decide which branch of init to run.

    Pure function — no side effects.
    """
    if not path.exists() or not (path / ".jig").is_dir():
        return DirState.FRESH
    project_yaml = path / ".jig" / "project.yaml"
    if not project_yaml.is_file():
        return DirState.BROKEN
    try:
        data = yaml.safe_load(project_yaml.read_text()) or {}
    except yaml.YAMLError:
        return DirState.BROKEN
    if not isinstance(data, dict):
        return DirState.BROKEN
    if not {"id", "name", "created_at"} <= data.keys():
        return DirState.BROKEN
    if data.get("template_applied_at"):
        return DirState.ALREADY_DONE
    return DirState.IN_PROGRESS


def create_stub(path: Path, *, name: str) -> None:
    """Create the minimal on-disk stub: ``.jig/project.yaml`` and
    ``.jig/spec/project.md``. Idempotent: never overwrites an existing
    project.yaml or brief.
    """
    path.mkdir(parents=True, exist_ok=True)
    (path / ".jig").mkdir(exist_ok=True)
    (path / ".jig" / "spec").mkdir(exist_ok=True)
    project_yaml = path / ".jig" / "project.yaml"
    if not project_yaml.is_file():
        data = {
            "id": str(uuid.uuid4()),
            "name": name,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        atomic_write_text(project_yaml, yaml.safe_dump(data, sort_keys=False))
    brief = path / ".jig" / "spec" / "project.md"
    if not brief.is_file():
        atomic_write_text(brief, f"# {name}\n")


async def run_init(*, name: str, force: bool) -> None:
    """Top-level init flow. Fleshed out across subsequent tasks.

    At this task stage, ``run_init`` only handles fresh / already-done /
    broken branches and creates the stub. PO spawn, spec-gen, branch
    prompt, SA, and scaffold are wired in later tasks.
    """
    target = Path(name)
    state = classify_directory(target)
    if state == DirState.ALREADY_DONE and not force:
        raise click.ClickException(
            f"{target} already initialized. Use --force to restart from scratch."
        )
    if state == DirState.BROKEN and not force:
        raise click.ClickException(
            f"{target}/.jig is in an inconsistent state. Use --force to reset."
        )
    if force and (target / ".jig").is_dir():
        _confirm_force(target)
        shutil.rmtree(target / ".jig")
    create_stub(target, name=name)
    click.echo(f"Initialized stub at {target}/.jig")
    # Later tasks wire the rest of the flow here.


def _confirm_force(target: Path) -> None:
    reply = click.prompt(
        f"This will wipe {target}/.jig. Type 'force' to continue",
        default="",
        show_default=False,
    )
    if reply != "force":
        raise click.ClickException("Aborted.")
