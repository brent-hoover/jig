"""Domain models for Jig."""

from enum import Enum

from pydantic import BaseModel


class MergeStrategy(str, Enum):
    DIRECT = "direct"
    SQUASH = "squash"
    PR = "pr"
    FEATURE_BRANCH = "feature_branch"


class RoleConfig(BaseModel):
    role: str
    phase_prompt: str
    response_prompt: str = ""
    allowed_tools: list[str] = []
    # NOTE: `can_message` was a stub with no enforcement and is removed
    # in Phase 1E. Cross-role messaging policy moves to capability policy
    # on role templates in Phase 5 (doc 16).
    default_context: list[str] = []
    allowed_mcps: list[str] = []


class PhaseConfig(BaseModel):
    name: str
    role: str
    task_template: str = ""
    acceptance_criteria: str = ""


class WorkflowConfig(BaseModel):
    name: str
    phases: list[PhaseConfig]
