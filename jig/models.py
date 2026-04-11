"""Domain models for Jig."""

from enum import Enum

from pydantic import BaseModel


class ProjectConfig(BaseModel):
    repo_path: str
    default_branch: str = "main"


class MergeStrategy(str, Enum):
    DIRECT = "direct"
    SQUASH = "squash"
    PR = "pr"
    FEATURE_BRANCH = "feature_branch"


class ProjectContext(BaseModel):
    name: str = ""
    description: str = ""
    language: str = ""
    framework: str = ""
    package_manager: str = ""
    template_path: str = ""
    setup_commands: list[str] = []
    build_command: str = ""
    test_command: str = ""
    merge_strategy: MergeStrategy = MergeStrategy.SQUASH
    docs: list[str] = []
    notes: str = ""


class AgentTypeConfig(BaseModel):
    role: str
    phase_prompt: str
    response_prompt: str = ""
    allowed_tools: list[str] = []
    can_message: list[str] = []
    default_context: list[str] = []


class PhaseConfig(BaseModel):
    name: str
    role: str
    task_template: str = ""
    acceptance_criteria: str = ""


class WorkflowConfig(BaseModel):
    name: str
    phases: list[PhaseConfig]
