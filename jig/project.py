"""Project runtime model — consumed by the orchestrator, prompt builder, skills.

Phase 1C: persistence moved from `.jig/project.json` to `.jig/config.yaml`
(see `jig.config.Config`). `save_project` / `load_project` preserve the
existing `Project` API but now read/write the nested yaml config. The
JSON file is still read (with a DeprecationWarning) during one release
cycle so pre-existing projects keep working.
"""

import json
import warnings
from pathlib import Path

from pydantic import BaseModel

from jig.models import MergeStrategy


class Project(BaseModel):
    id: str
    name: str
    path: str
    default_branch: str = "main"
    description: str = ""
    language: str = ""
    framework: str = ""
    package_manager: str = ""
    test_command: str = ""
    build_command: str = ""
    merge_strategy: MergeStrategy = MergeStrategy.FEATURE_BRANCH

    def path_or_default(self) -> Path:
        return Path(self.path)


def _config_file(project_path: Path) -> Path:
    return project_path / ".jig" / "config.yaml"


def _legacy_json_file(project_path: Path) -> Path:
    return project_path / ".jig" / "project.json"


def save_project(project_path: Path, project: Project) -> None:
    """Write the project section of `.jig/config.yaml`.

    Preserves existing non-project sections (workflows, ownership, roles,
    escalation) if the file already exists.
    """
    # Lazy-imported to avoid circular import: jig.config imports Project.
    from jig.config import Config, load_config, save_config

    try:
        config = load_config(project_path)
        config.project = project
    except FileNotFoundError:
        config = Config(project=project)
    save_config(project_path, config)


def load_project(project_path: Path) -> Project:
    """Read the project section from `.jig/config.yaml`.

    Falls back to the legacy `.jig/project.json` with a DeprecationWarning
    if config.yaml is absent. Raises FileNotFoundError if neither file
    exists.
    """
    from jig.config import load_config

    try:
        return load_config(project_path).project
    except FileNotFoundError:
        pass

    legacy = _legacy_json_file(project_path)
    if legacy.is_file():
        warnings.warn(
            f"{legacy} is deprecated; migrate to .jig/config.yaml. "
            "The JSON fallback will be removed in a future release.",
            DeprecationWarning,
            stacklevel=2,
        )
        return Project.model_validate_json(legacy.read_text())

    raise FileNotFoundError(
        f"No project config found at {_config_file(project_path)} "
        f"or {legacy}"
    )


def _json_save_project(project_path: Path, project: Project) -> None:
    """Legacy JSON writer — retained for migration tests only."""
    path = _legacy_json_file(project_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(project.model_dump(), indent=2))
