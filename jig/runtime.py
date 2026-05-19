from collections.abc import Callable
from dataclasses import dataclass, field
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
    CONFLICT_RESOLVER = "conflict_resolver"
    REPLAN = "replan"
    # fix-loop-context — back-routed phase agent after a review block.
    # Same role as PHASE_PRIMARY would have been, but carries the
    # ``fix_loop_bundle`` with the latest cycle's blocking findings
    # routed to this phase plus any prior addressed-claim history.
    # Prompt builder renders the "Blocking Findings" section only
    # when reason is FIX_LOOP_RETRY.
    FIX_LOOP_RETRY = "fix_loop_retry"


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
    # Optional so call sites without a checkpoint store still work.
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
    # Called on every agent_thinking heartbeat so the orchestrator's
    # StallDetector can track liveness without going through the WS layer.
    on_thinking: Callable[[], None] | None = field(default=None, repr=False)
    # fix-loop-context — pre-built bundle of blocking findings targeted
    # at this phase plus their prior addressed-claim history. Populated
    # only when spawn_reason is FIX_LOOP_RETRY. Opaque dict shape so
    # the runtime module doesn't pull in the prompt-builder types.
    fix_loop_bundle: dict | None = None
    # fix-loop-context — pre-built bundle of all prior cycle findings +
    # acks for the ticket, rendered into the reviewer's prompt as the
    # "Previous Cycle Findings" section. Populated on cycle 2+ when the
    # ticket has any acks. None on cycle 1.
    verify_bundle: dict | None = None
    # fix-loop-context — current fix-loop cycle (0 = first review pass,
    # 1+ = post-block re-runs). Threaded into the MCP server so
    # mark_finding_addressed / mark_finding_resolved get the right cycle
    # stamped without the agent having to pass it.
    cycle: int = 0
