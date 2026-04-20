from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from jig.models import PhaseConfig, RoleConfig
from jig.project import Project
from jig.store import MessageBus
from jig.store.checkpoints import CheckpointStore
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
    threads: ThreadStore
    memory: MemoryStore
    bus: MessageBus
    # Phase 4 Task G — checkpoint store plumbed through the agent context
    # so the MCP server can record milestone / decision / deferred and
    # the harness-triggered commit/test/pre-handoff hooks fire. Optional
    # so call sites that don't build a checkpoint store (older tests,
    # one-shot operator spawns) still work.
    checkpoints: CheckpointStore | None = None
    # Populated for PHASE_PRIMARY spawns — None for QA_RESPONDER and other
    # thread-level spawns where there is no workflow phase context.
    phase: PhaseConfig | None = None
    initial_bus_message: dict | None = None
