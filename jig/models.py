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
    can_message: list[str] = []
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
