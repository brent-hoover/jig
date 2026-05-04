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
    # Phase 5 Task O2b — evaluator agent spawned on gate-pass for a
    # role-kind phase evaluator. The agent reviews the pending handoff
    # and accepts/rejects it via thread_mcp tools. Distinct from
    # PHASE_PRIMARY so prompt_builder can key off the reason to compose
    # an evaluator-specific prompt (not the phase's normal work prompt).
    EVALUATOR = "evaluator"


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
    # Track E MVP — per-agent env map injected into the SDK options. The
    # orchestrator's ``_run_agent_with_analytics`` populates this with
    # the connection-string map returned by ``provision_agent_namespace``
    # before invoking ``run_agent``. None / empty means no extra env.
    extra_env: dict[str, str] | None = None
    # Block 2 — analytics emitter forwarded into the MCP server factory
    # so analytics-emitting MCP tool handlers (ontology edits, etc.)
    # actually emit when invoked from a real agent. Typed as ``object``
    # to avoid pulling the analytics module into the runtime import
    # graph; ``mcp_server.create_agent_mcp_server`` narrows back.
    analytics_emitter: object | None = None
