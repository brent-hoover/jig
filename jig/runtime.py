from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from jig.models import RoleConfig
from jig.project import Project
from jig.store import MessageBus
from jig.store.comments import CommentStore
from jig.store.memory import MemoryStore
from jig.store.tickets import TicketStore
from jig.ticket import Ticket


class SpawnReason(str, Enum):
    PHASE_PRIMARY = "phase_primary"
    QA_RESPONDER = "qa_responder"


@dataclass
class AgentSpawnContext:
    role: str
    role_cfg: RoleConfig
    spawn_reason: SpawnReason
    ticket: Ticket
    parent: Ticket | None
    worktree_path: Path
    project: Project
    tickets: TicketStore
    comments: CommentStore
    memory: MemoryStore
    bus: MessageBus
    initial_bus_message: dict | None = None
