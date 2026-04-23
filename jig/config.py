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
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from jig.project import Project

# Public so tests/docs can reference the canonical vocabularies.
RoleAssignmentKind = Literal["", "human", "human_with_helper", "agent"]
SelfApprovalPolicy = Literal["warn", "blocked"]


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
    """How a project-level role (PO/SA) is staffed.

    ``assignment`` is a ``Literal`` — typos fail loud at
    ``Config.model_validate``/``load_config`` time rather than
    silently misbehaving later. Empty string means "unset".
    """

    assignment: RoleAssignmentKind = ""
    human: str = ""
    helper_template: str = ""


class RolesSection(BaseModel):
    """PO / SA (and any extras) role wiring."""

    model_config = ConfigDict(extra="allow")

    po: RoleAssignment | None = None
    sa: RoleAssignment | None = None


class EscalationSection(BaseModel):
    default_human: str = ""


class DeadlockSection(BaseModel):
    """Age-based deadlock auto-resolution thresholds (Phase 5 Task L).

    Seconds rather than human strings so the sweep can diff
    ``datetime`` values directly. Defaults match doc 08's
    orchestrator-as-resolver-of-last-resort expectations:

    * ``nudge_after_s`` — 4 hours. An open blocking thread entry
      older than this triggers a Note tagging the target actor.
    * ``escalate_after_s`` — 24 hours. An open blocking entry past
      this triggers an Escalation to ``any_human`` and flips the
      ticket to ``needs_info``.

    Set either to ``0`` to disable that tier (useful when operating
    purely on human cadence). Per-phase overrides are not wired yet —
    Phase 5 ships project-wide values; doc 08 leaves the per-phase
    surface as an open extension.
    """

    nudge_after_s: int = 4 * 3600
    escalate_after_s: int = 24 * 3600


class Config(BaseModel):
    """Top-level config for a jig project, persisted as `.jig/config.yaml`."""

    project: Project
    workflows: WorkflowsSection = Field(default_factory=WorkflowsSection)
    ownership: OwnershipSection = Field(default_factory=OwnershipSection)
    roles: RolesSection = Field(default_factory=RolesSection)
    escalation: EscalationSection = Field(default_factory=EscalationSection)
    deadlock: DeadlockSection = Field(default_factory=DeadlockSection)
    # Phase 3G self-certification policy per doc 04.
    #   "warn"    — allow but record "self_approval_with_justification"
    #               (shipped default — solo devs need the escape hatch).
    #   "blocked" — refuse if proposer == acceptor.
    self_approval: SelfApprovalPolicy = "warn"


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


class WorkflowResolutionError(ValueError):
    """Raised when an explicit workflow name is disallowed by config."""


def resolve_workflow(
    config: Config,
    *,
    work_type: str,
    size: str,
    explicit: str | None = None,
) -> str:
    """Pick a workflow name for a ticket per doc 17 resolution order.

    Order:

    1. ``explicit`` (if supplied). Must be in
       ``config.workflows.available`` when that list is populated,
       otherwise a :class:`WorkflowResolutionError` is raised.
    2. ``config.workflows.by_type.<work_type>.default_by_size.<size>``.
    3. ``config.workflows.by_type.<work_type>.available[0]`` if the
       per-type list is single-valued.
    4. ``config.workflows.default_by_size.<size>``.
    5. ``"default"`` — the historical fallback.

    Steps 2–4 are filtered by ``config.workflows.available`` when set;
    a resolved name not in ``available`` falls through to the next
    step instead of being rejected (it's a config inconsistency that
    Phase 2F surfaces separately).
    """
    available = set(config.workflows.available)

    def _allowed(name: str) -> bool:
        return not available or name in available

    if explicit:
        if not _allowed(explicit):
            raise WorkflowResolutionError(
                f"workflow {explicit!r} is not in workflows.available "
                f"({sorted(available)})"
            )
        return explicit

    by_type = config.workflows.by_type.get(work_type)
    if by_type is not None:
        name = by_type.default_by_size.get(size)
        if name and _allowed(name):
            return name
        if len(by_type.available) == 1 and _allowed(by_type.available[0]):
            return by_type.available[0]

    name = config.workflows.default_by_size.get(size)
    if name and _allowed(name):
        return name

    return "default"


def validate_workflow_references(
    config: Config, known_workflows: list[str]
) -> list[str]:
    """Check workflow references in config against the shipped catalog.

    Returns a list of human-readable warning messages for references
    that point at a workflow name not present in `known_workflows`.
    Empty list means the config is consistent.

    Phase 1 behaviour is advisory: callers should print/warn but not
    abort. Phase 2 upgrades these to hard errors once the full
    resolver lands.
    """
    known = set(known_workflows)
    warnings: list[str] = []

    def _check(name: str, where: str) -> None:
        if name and name not in known:
            warnings.append(
                f"workflows.{where} references unknown workflow "
                f"{name!r} (known: {sorted(known)})"
            )

    # Project-wide defaults and roster.
    for size, wf in config.workflows.default_by_size.items():
        _check(wf, f"default_by_size.{size}")
    for wf in config.workflows.available:
        _check(wf, "available")

    # Per-work-type overrides.
    for work_type, entry in config.workflows.by_type.items():
        for size, wf in entry.default_by_size.items():
            _check(wf, f"by_type.{work_type}.default_by_size.{size}")
        for wf in entry.available:
            _check(wf, f"by_type.{work_type}.available")

    return warnings


__all__ = [
    "Config",
    "DeadlockSection",
    "EscalationSection",
    "OwnershipSection",
    "RoleAssignment",
    "RolesSection",
    "SpecOwnership",
    "WorkflowResolutionError",
    "WorkflowTypeEntry",
    "WorkflowsSection",
    "load_config",
    "resolve_workflow",
    "save_config",
    "validate_workflow_references",
]
