"""File I/O for .jig/ directory structure."""

from pathlib import Path

import yaml

from jig.models import (
    AgentTypeConfig,
    PhaseConfig,
    WorkflowConfig,
)


def _jig_dir(project_path: Path) -> Path:
    return project_path / ".jig"


def init_project(project_path: Path, default_branch: str = "main") -> None:
    """Initialize .jig/ directory in a project."""
    if not (project_path / ".git").is_dir():
        raise ValueError(f"{project_path} is not a git repository")

    jig_dir = _jig_dir(project_path)
    if jig_dir.exists():
        raise FileExistsError(f"{jig_dir} already exists")

    jig_dir.mkdir()
    for subdir in ("agent_types", "workflows", "worktrees", "store"):
        (jig_dir / subdir).mkdir()


def save_agent_type(project_path: Path, config: AgentTypeConfig) -> None:
    """Save an agent type config to .jig/agent_types/<role>.yaml."""
    type_path = _jig_dir(project_path) / "agent_types" / f"{config.role}.yaml"
    type_path.write_text(
        yaml.dump(config.model_dump(), default_flow_style=False)
    )


def load_agent_type(project_path: Path, name: str) -> AgentTypeConfig:
    """Load an agent type config from .jig/agent_types/<name>.yaml."""
    type_path = _jig_dir(project_path) / "agent_types" / f"{name}.yaml"
    if not type_path.is_file():
        raise FileNotFoundError(f"Agent type '{name}' not found")
    data = yaml.safe_load(type_path.read_text())
    return AgentTypeConfig.model_validate(data)


def list_agent_types(project_path: Path) -> list[AgentTypeConfig]:
    """List all agent type configs in .jig/agent_types/."""
    types_dir = _jig_dir(project_path) / "agent_types"
    if not types_dir.is_dir():
        return []
    configs = []
    for yaml_file in sorted(types_dir.glob("*.yaml")):
        data = yaml.safe_load(yaml_file.read_text())
        configs.append(AgentTypeConfig.model_validate(data))
    return configs


def _defaults_dir() -> Path:
    """Return the path to the built-in defaults directory."""
    return Path(__file__).resolve().parent / "defaults"


def save_default_agent_types(project_path: Path) -> None:
    """Copy default agent type configs from jig/defaults/agent_types/ into project."""
    source_dir = _defaults_dir() / "agent_types"
    for yaml_file in sorted(source_dir.glob("*.yaml")):
        data = yaml.safe_load(yaml_file.read_text())
        config = AgentTypeConfig.model_validate(data)
        save_agent_type(project_path, config)


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
    "AgentTypeConfig",
    "PhaseConfig",
    "WorkflowConfig",
    "_jig_dir",
    "init_project",
    "list_agent_types",
    "load_agent_type",
    "load_workflow",
    "save_agent_type",
    "save_default_agent_types",
    "save_default_workflow",
    "save_workflow",
]
