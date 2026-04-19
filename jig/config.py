"""Project-level configuration — `.jig/config.yaml`.

Per doc 17. The on-disk shape is nested:

    project:
      name: my-project
      scm: {adapter: github, repo: org/my-project}
      ...
    workflows:
      default_by_size: {xs: hotfix, s: small-change, ...}
      available: [hotfix, small-change, ...]
      by_type: {feature: {default_by_size: {...}, available: [...]}}
    ownership: {...}
    roles: {...}
    escalation: {default_human: alice@example.com}

Phase 1 parses all sections. Only `project:` is consumed by the runtime
today (via `jig.project.Project`). `workflows.by_type` is surfaced for
Phase 2's workflow resolver. `ownership` / `roles` / `escalation` are
accepted but not yet enforced (Phase 3).
"""

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from jig.project import Project


class WorkflowTypeEntry(BaseModel):
    """Workflow selection for a single work type."""

    default_by_size: dict[str, str] = Field(default_factory=dict)
    available: list[str] = Field(default_factory=list)


class WorkflowsSection(BaseModel):
    """Workflow catalog resolution policy."""

    default_by_size: dict[str, str] = Field(default_factory=dict)
    available: list[str] = Field(default_factory=list)
    by_type: dict[str, WorkflowTypeEntry] = Field(default_factory=dict)


class SpecOwnership(BaseModel):
    """Who owns which field of the structured spec."""

    model_config = ConfigDict(extra="allow")

    behaviors: str = "po"
    acceptance_criteria: str = "po"
    design: str = "sa"
    technical_risks: str = "sa"


class OwnershipSection(BaseModel):
    """Project-level ownership assignments (per doc 04)."""

    model_config = ConfigDict(extra="allow")

    spec: SpecOwnership = Field(default_factory=SpecOwnership)
    architecture: str = "sa"
    capability_policy: str = "sa"
    process_policy: str = "sa"
    content_policy: str = "sa"
    roadmap: str = "po"


class RoleAssignment(BaseModel):
    """How a project-level role (PO/SA) is staffed."""

    assignment: str = ""  # "human", "human_with_helper", "agent"
    human: str = ""
    helper_template: str = ""


class RolesSection(BaseModel):
    """PO / SA (and any extras) role wiring."""

    model_config = ConfigDict(extra="allow")

    po: RoleAssignment | None = None
    sa: RoleAssignment | None = None


class EscalationSection(BaseModel):
    default_human: str = ""


class Config(BaseModel):
    """Top-level config for a jig project, persisted as `.jig/config.yaml`."""

    project: Project
    workflows: WorkflowsSection = Field(default_factory=WorkflowsSection)
    ownership: OwnershipSection = Field(default_factory=OwnershipSection)
    roles: RolesSection = Field(default_factory=RolesSection)
    escalation: EscalationSection = Field(default_factory=EscalationSection)


def _config_file(project_path: Path) -> Path:
    return project_path / ".jig" / "config.yaml"


def save_config(project_path: Path, config: Config) -> None:
    """Write `.jig/config.yaml`."""
    path = _config_file(project_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(config.model_dump(mode="json"), sort_keys=False))


def load_config(project_path: Path) -> Config:
    """Read `.jig/config.yaml`. Raises FileNotFoundError if absent."""
    path = _config_file(project_path)
    if not path.is_file():
        raise FileNotFoundError(f"Config file not found: {path}")
    data = yaml.safe_load(path.read_text()) or {}
    return Config.model_validate(data)


__all__ = [
    "Config",
    "EscalationSection",
    "OwnershipSection",
    "RoleAssignment",
    "RolesSection",
    "SpecOwnership",
    "WorkflowTypeEntry",
    "WorkflowsSection",
    "load_config",
    "save_config",
]
