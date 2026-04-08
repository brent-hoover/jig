"""Domain models for Jig."""

from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field


class IssueStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class CompletionState(str, Enum):
    SUCCESS = "success"
    NEEDS_INFO = "needs_info"
    BLOCKED = "blocked"
    FAILED = "failed"


class MessageType(str, Enum):
    TASK_ASSIGNMENT = "task_assignment"
    TASK_COMPLETION = "task_completion"
    QUESTION = "question"
    ANSWER = "answer"
    CONTEXT_UPDATE = "context_update"
    STATUS = "status"


class ProjectConfig(BaseModel):
    repo_path: str
    default_branch: str = "main"


class Issue(BaseModel):
    id: str
    title: str
    status: IssueStatus = IssueStatus.PENDING
    current_phase: str | None = None
    base_branch: str = "main"


class Task(BaseModel):
    id: str
    description: str
    acceptance_criteria: str
    agent_type: str
    input_context: list[str] = []
    completion_state: CompletionState | None = None
    completion_reason: str | None = None


class Message(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    sender: str
    recipient: str
    type: MessageType
    payload: dict = {}
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    correlation_id: str | None = None


class AgentTypeConfig(BaseModel):
    name: str
    system_prompt: str
    allowed_tools: list[str] = []
    denied_tools: list[str] = []
    default_context: list[str] = []


class WorkflowPhase(str, Enum):
    SPEC = "spec"
    TEST = "test"
    IMPLEMENT = "implement"
    REVIEW = "review"


class PhaseConfig(BaseModel):
    name: WorkflowPhase
    agent_type: str


class WorkflowConfig(BaseModel):
    name: str
    phases: list[PhaseConfig]
