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


class Issue(BaseModel):
    id: str
    title: str
    description: str = ""
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
    role: str
    system_prompt: str
    allowed_tools: list[str] = []
    default_context: list[str] = []


class PhaseConfig(BaseModel):
    name: str
    role: str
    task_template: str = ""
    acceptance_criteria: str = ""


class WorkflowConfig(BaseModel):
    name: str
    phases: list[PhaseConfig]


class PhaseHistoryEntry(BaseModel):
    """A persisted record of a phase execution attempt."""
    phase: str
    agent_type: str
    result: str  # "success", "needs_info", "blocked", "failed", "error"
    branch: str | None = None
    reason: str = ""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MessageDirection(str, Enum):
    REQUEST = "request"
    RESPONSE = "response"


class AgentMessage(BaseModel):
    """Structured message between agents."""
    id: str = Field(default_factory=lambda: str(uuid4()))
    sender_id: str
    recipient_id: str
    direction: MessageDirection
    topic: str
    content: str
    correlation_id: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AgentStatus(str, Enum):
    IDLE = "idle"
    ACTIVE = "active"
    DORMANT = "dormant"


class AgentInstance(BaseModel):
    """A running or dormant agent spawned from an agent type."""
    id: str
    agent_type: str
    status: AgentStatus = AgentStatus.IDLE
    session_id: str | None = None
    current_task_id: str | None = None
    memory: list[str] = []


class CompletionStatus(str, Enum):
    SUCCESS = "success"
    NEEDS_INFO = "needs_info"
    BLOCKED = "blocked"
    FAILED = "failed"


class CompletionReport(BaseModel):
    """Structured report when an agent finishes (or cannot finish) a task."""
    agent_id: str
    task_id: str
    status: CompletionStatus
    summary: str
    reason: str = ""
    artifacts: list[str] = []
    needs_from: str | None = None
    question: str | None = None
