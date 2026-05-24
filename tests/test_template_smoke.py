"""Template smoke-test loop.

For each bundled template under ``jig/defaults/project_templates/``:

1. Scaffold it into a fresh ``tmp_path`` via ``_apply_template_files``.
2. ``uv sync`` to install the project + dev deps + the project itself
   (every template ships ``[build-system]`` with hatchling so the
   project is installable; tests can ``import myproject``).
3. ``uv run pytest -q`` inside the scaffolded project.

Any non-zero exit fails the test. A template with no collectable
tests fails too (``pytest`` exits 5 = "no tests collected") — that's
intentional: every shipped template must declare what "successfully
scaffolded" means for it.

Network-light: the only network access is ``uv``'s dependency download.
Locally cached; CI hits a clean cache on first run per branch.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from jig.init_workflow import _apply_template_files

_TEMPLATES_ROOT = (
    Path(__file__).resolve().parents[1]
    / "jig"
    / "defaults"
    / "project_templates"
)


def _discover_templates() -> list[str]:
    """Return the names of every shipped template directory."""
    if not _TEMPLATES_ROOT.is_dir():
        return []
    return sorted(p.name for p in _TEMPLATES_ROOT.iterdir() if p.is_dir())


@pytest.mark.skipif(
    shutil.which("uv") is None,
    reason="uv not on PATH — template smoke needs uv to sync + run pytest",
)
@pytest.mark.parametrize("template_name", _discover_templates())
def test_template_scaffolds_and_smoke_passes(
    template_name: str, tmp_path: Path
) -> None:
    """Scaffold ``template_name``, install it, run its smoke test."""
    dest = tmp_path / "scaffolded"
    _apply_template_files(
        template_name=template_name,
        dest=dest,
        project_name="scaffold_smoke",
    )

    sync = subprocess.run(
        ["uv", "sync", "--quiet"],
        cwd=dest,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert sync.returncode == 0, (
        f"uv sync failed for {template_name}:\n"
        f"--- stdout ---\n{sync.stdout}\n--- stderr ---\n{sync.stderr}"
    )

    run = subprocess.run(
        ["uv", "run", "pytest", "-q"],
        cwd=dest,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert run.returncode == 0, (
        f"smoke test failed for {template_name}:\n"
        f"--- stdout ---\n{run.stdout}\n--- stderr ---\n{run.stderr}"
    )
