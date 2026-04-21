"""File I/O for .jig/ directory structure.

Catalog resolution (Phase 2 Task C) is two-layered per doc 17 §Resolution
order:

1. Project repo under ``<project>/.jig/<kind>/<name>.yaml`` — wins if
   present.
2. Shipped default under ``jig/defaults/<kind>/<name>.yaml`` — the
   fallback.

Neither file exists → ``FileNotFoundError`` with both paths named.

Shipped defaults are *not* pre-copied into the project at ``jig init``
anymore; they're served straight from the installed package at runtime.
Teams that want to customize copy the file themselves (a future
``jig role init <name>`` scaffold). ``save_default_roles`` /
``save_default_workflow`` are kept as library helpers for that use case.
"""

from pathlib import Path

import yaml

from jig.models import (
    RoleConfig,
    PhaseConfig,
    WorkflowConfig,
)


def _jig_dir(project_path: Path) -> Path:
    return project_path / ".jig"


def _defaults_dir() -> Path:
    """Return the path to the built-in defaults directory."""
    return Path(__file__).resolve().parent / "defaults"


def init_project(project_path: Path, default_branch: str = "main") -> None:
    """Initialize `.jig/` with the doc-17 directory layout."""
    if not (project_path / ".git").is_dir():
        raise ValueError(f"{project_path} is not a git repository")

    jig_dir = _jig_dir(project_path)
    if jig_dir.exists():
        raise FileExistsError(f"{jig_dir} already exists")

    jig_dir.mkdir()

    # Operational dirs. roles/workflows/work_types start empty — fallbacks
    # come from jig.defaults at runtime. The dirs exist so overrides have
    # an obvious home.
    for subdir in ("roles", "workflows", "work_types", "worktrees", "store"):
        (jig_dir / subdir).mkdir()

    # Doc-17 placeholders — populated in later phases but laid down now
    # so the catalog resolver (phase 2) and spec flows (phase 3) find a
    # consistent shape on fresh projects.
    #   spec/              — PO-authored project spec (phase 3)
    #   context/project/   — project-wide context artifacts (phase 2)
    #   context/roles/     — per-role context overlays (phase 2)
    #   decisions/         — decision records (phase 3)
    #   archive/           — closed-ticket archives
    for subdir in (
        "spec",
        "specs",  # per-ticket structured specs (phase 3 task C)
        "context",
        "context/project",
        "context/roles",
        "decisions",
        "archive",
    ):
        (jig_dir / subdir).mkdir(parents=True)

    # Empty check catalog — phase 5 populates real checks.
    checks_path = jig_dir / "checks.yaml"
    checks_path.write_text(
        "# Check catalog — see docs/10-verification.md.\n"
        "# Populated in phase 5.\n"
        "checks: {}\n"
    )


# ---- roles ----------------------------------------------------------------


def save_role(project_path: Path, config: RoleConfig) -> None:
    """Save a role config to .jig/roles/<role>.yaml."""
    type_path = _jig_dir(project_path) / "roles" / f"{config.role}.yaml"
    type_path.parent.mkdir(parents=True, exist_ok=True)
    type_path.write_text(
        yaml.dump(config.model_dump(), default_flow_style=False)
    )


def _role_path_project(project_path: Path, name: str) -> Path:
    return _jig_dir(project_path) / "roles" / f"{name}.yaml"


def _role_path_shipped(name: str) -> Path:
    return _defaults_dir() / "roles" / f"{name}.yaml"


def load_role(project_path: Path, name: str) -> RoleConfig:
    """Load a role config, preferring the project override over the shipped default."""
    project_path_file = _role_path_project(project_path, name)
    if project_path_file.is_file():
        data = yaml.safe_load(project_path_file.read_text())
        return RoleConfig.model_validate(data)
    shipped_path = _role_path_shipped(name)
    if shipped_path.is_file():
        data = yaml.safe_load(shipped_path.read_text())
        return RoleConfig.model_validate(data)
    raise FileNotFoundError(
        f"role {name!r} not found (looked in {project_path_file} and {shipped_path})"
    )


def list_roles(project_path: Path) -> list[RoleConfig]:
    """List all roles available to this project.

    Merges the project override layer with the shipped defaults. Project
    files win when both layers define the same role name.
    """
    seen: dict[str, RoleConfig] = {}

    project_dir = _jig_dir(project_path) / "roles"
    if project_dir.is_dir():
        for yaml_file in sorted(project_dir.glob("*.yaml")):
            data = yaml.safe_load(yaml_file.read_text())
            config = RoleConfig.model_validate(data)
            seen[config.role] = config

    shipped_dir = _defaults_dir() / "roles"
    if shipped_dir.is_dir():
        for yaml_file in sorted(shipped_dir.glob("*.yaml")):
            data = yaml.safe_load(yaml_file.read_text())
            config = RoleConfig.model_validate(data)
            seen.setdefault(config.role, config)

    return [seen[name] for name in sorted(seen)]


def list_role_names(project_path: Path) -> list[str]:
    """Return every role name this project can resolve (project + defaults)."""
    names: set[str] = set()
    project_dir = _jig_dir(project_path) / "roles"
    if project_dir.is_dir():
        names.update(p.stem for p in project_dir.glob("*.yaml"))
    shipped_dir = _defaults_dir() / "roles"
    if shipped_dir.is_dir():
        names.update(p.stem for p in shipped_dir.glob("*.yaml"))
    return sorted(names)


def save_default_roles(project_path: Path) -> None:
    """Copy shipped role templates into the project.

    No longer called by ``jig init`` (Phase 2 Task C) — runtime resolves
    shipped defaults on demand. Kept as a library helper so a future
    ``jig role init <name>`` command can scaffold one into the project
    for editing.
    """
    source_dir = _defaults_dir() / "roles"
    for yaml_file in sorted(source_dir.glob("*.yaml")):
        data = yaml.safe_load(yaml_file.read_text())
        config = RoleConfig.model_validate(data)
        save_role(project_path, config)


# ---- workflows ------------------------------------------------------------


def save_workflow(project_path: Path, workflow: WorkflowConfig) -> None:
    """Save a workflow config to .jig/workflows/<name>.yaml."""
    wf_path = _jig_dir(project_path) / "workflows" / f"{workflow.name}.yaml"
    wf_path.parent.mkdir(parents=True, exist_ok=True)
    wf_path.write_text(
        yaml.dump(workflow.model_dump(mode="json"), default_flow_style=False)
    )


def _workflow_path_project(project_path: Path, name: str) -> Path:
    return _jig_dir(project_path) / "workflows" / f"{name}.yaml"


def _workflow_path_shipped(name: str) -> Path:
    return _defaults_dir() / "workflows" / f"{name}.yaml"


def load_workflow(project_path: Path, name: str) -> WorkflowConfig:
    """Load a workflow config, preferring the project override over the shipped default."""
    project_path_file = _workflow_path_project(project_path, name)
    if project_path_file.is_file():
        data = yaml.safe_load(project_path_file.read_text())
        return WorkflowConfig.model_validate(data)
    shipped_path = _workflow_path_shipped(name)
    if shipped_path.is_file():
        data = yaml.safe_load(shipped_path.read_text())
        return WorkflowConfig.model_validate(data)
    raise FileNotFoundError(
        f"workflow {name!r} not found (looked in {project_path_file} and {shipped_path})"
    )


def list_workflow_names(project_path: Path) -> list[str]:
    """Return every workflow name resolvable by this project (project + defaults)."""
    names: set[str] = set()
    project_dir = _jig_dir(project_path) / "workflows"
    if project_dir.is_dir():
        names.update(p.stem for p in project_dir.glob("*.yaml"))
    shipped_dir = _defaults_dir() / "workflows"
    if shipped_dir.is_dir():
        names.update(p.stem for p in shipped_dir.glob("*.yaml"))
    return sorted(names)


def save_default_workflow(project_path: Path) -> None:
    """Copy shipped workflow templates into the project.

    No longer called by ``jig init`` — runtime resolves shipped defaults
    on demand. Kept as a scaffolding helper; see ``save_default_roles``.
    """
    source_dir = _defaults_dir() / "workflows"
    for yaml_file in sorted(source_dir.glob("*.yaml")):
        data = yaml.safe_load(yaml_file.read_text())
        workflow = WorkflowConfig.model_validate(data)
        save_workflow(project_path, workflow)


__all__ = [
    "RoleConfig",
    "PhaseConfig",
    "WorkflowConfig",
    "_jig_dir",
    "init_project",
    "list_role_names",
    "list_roles",
    "list_workflow_names",
    "load_role",
    "load_workflow",
    "save_role",
    "save_default_roles",
    "save_default_workflow",
    "save_workflow",
]
