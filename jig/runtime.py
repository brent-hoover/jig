from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from jig.models import PhaseConfig, RoleConfig
from jig.project import Project
from jig.store import MessageBus
from jig.store.comments import CommentStore
from jig.store.memory import MemoryStore
from jig.store.threads import ThreadStore
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
    threads: ThreadStore
    memory: MemoryStore
    bus: MessageBus
    # Populated for PHASE_PRIMARY spawns — None for QA_RESPONDER and other
    # thread-level spawns where there is no workflow phase context.
    phase: PhaseConfig | None = None
    initial_bus_message: dict | None = None
