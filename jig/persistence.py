"""File I/O for .jig/ directory structure."""

from pathlib import Path

import yaml

from jig.models import (
    RoleConfig,
    PhaseConfig,
    WorkflowConfig,
)


def _jig_dir(project_path: Path) -> Path:
    return project_path / ".jig"


def init_project(project_path: Path, default_branch: str = "main") -> None:
    """Initialize `.jig/` with the doc-17 directory layout."""
    if not (project_path / ".git").is_dir():
        raise ValueError(f"{project_path} is not a git repository")

    jig_dir = _jig_dir(project_path)
    if jig_dir.exists():
        raise FileExistsError(f"{jig_dir} already exists")

    jig_dir.mkdir()

    # Operational dirs consumed today (roles/workflows by the runtime;
    # worktrees/store by git and the JSONL stores respectively).
    for subdir in ("roles", "workflows", "worktrees", "store"):
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


def save_role(project_path: Path, config: RoleConfig) -> None:
    """Save an agent type config to .jig/roles/<role>.yaml."""
    type_path = _jig_dir(project_path) / "roles" / f"{config.role}.yaml"
    type_path.write_text(
        yaml.dump(config.model_dump(), default_flow_style=False)
    )


def load_role(project_path: Path, name: str) -> RoleConfig:
    """Load an agent type config from .jig/roles/<name>.yaml."""
    type_path = _jig_dir(project_path) / "roles" / f"{name}.yaml"
    if not type_path.is_file():
        raise FileNotFoundError(f"Agent type '{name}' not found")
    data = yaml.safe_load(type_path.read_text())
    return RoleConfig.model_validate(data)


def list_roles(project_path: Path) -> list[RoleConfig]:
    """List all agent type configs in .jig/roles/."""
    types_dir = _jig_dir(project_path) / "roles"
    if not types_dir.is_dir():
        return []
    configs = []
    for yaml_file in sorted(types_dir.glob("*.yaml")):
        data = yaml.safe_load(yaml_file.read_text())
        configs.append(RoleConfig.model_validate(data))
    return configs


def _defaults_dir() -> Path:
    """Return the path to the built-in defaults directory."""
    return Path(__file__).resolve().parent / "defaults"


def save_default_roles(project_path: Path) -> None:
    """Copy default agent type configs from jig/defaults/roles/ into project."""
    source_dir = _defaults_dir() / "roles"
    for yaml_file in sorted(source_dir.glob("*.yaml")):
        data = yaml.safe_load(yaml_file.read_text())
        config = RoleConfig.model_validate(data)
        save_role(project_path, config)


def save_workflow(project_path: Path, workflow: WorkflowConfig) -> None:
    """Save a workflow config to .jig/workflows/<name>.yaml."""
    wf_path = _jig_dir(project_path) / "workflows" / f"{workflow.name}.yaml"
    wf_path.write_text(
        yaml.dump(workflow.model_dump(mode="json"), default_flow_style=False)
    )


def load_workflow(project_path: Path, name: str) -> WorkflowConfig:
    """Load a workflow config from .jig/workflows/<name>.yaml."""
    wf_path = _jig_dir(project_path) / "workflows" / f"{name}.yaml"
    if not wf_path.is_file():
        raise FileNotFoundError(f"Workflow '{name}' not found")
    data = yaml.safe_load(wf_path.read_text())
    return WorkflowConfig.model_validate(data)


def save_default_workflow(project_path: Path) -> None:
    """Copy default workflow config from jig/defaults/workflows/ into project."""
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
    "list_roles",
    "load_role",
    "load_workflow",
    "save_role",
    "save_default_roles",
    "save_default_workflow",
    "save_workflow",
]
