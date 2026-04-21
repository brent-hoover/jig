"""Checkpoint channel models per doc 09.

Checkpoints are the separate-from-thread channel that captures an
agent's in-flight state: what was completed, where it is, what's next,
what was ruled out, what got deferred, what's still an open question.
Harness-triggered checkpoints fire from commit and lint/test paths and
a pre-handoff hook; agent-triggered checkpoints come from the
``checkpoint_milestone`` / ``checkpoint_decision`` / ``checkpoint_deferred``
MCP tools.

Doc 09 keeps this channel separate from the thread because the audit
semantics differ — thread entries are conversations with gating
semantics, checkpoints are diary/position notes. The ``DeferredItem``
type is shared with the thread layer: Task G writes them as checkpoint
side-effects, Task F packages the current phase's open items into the
Handoff entry so the evaluator sees them.

``historical`` is flipped to True when a phase accepts its Handoff
(Task G §Phase-boundary pruning) — historical checkpoints stay on
disk for audit but are excluded from ``latest`` / ``for_phase``
queries by default.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

from jig.store.models import StoreModel
from jig.thread import DeferredItem  # shared value object — see thread.py


class RuledOut(BaseModel):
    """An approach the agent considered and rejected, with reasoning.

    Captured in checkpoints so future spawns or retries don't re-tread
    dead ends without the context that ruled them out the first time.
    """

    approach: str
    reason: str


# Trigger taxonomy per doc 09. Harness events use ``auto_*``; agent
# MCP calls use ``agent_*``.
CheckpointTrigger = Literal[
    "auto_commit",
    "auto_test",
    "auto_pre_handoff",
    "agent_milestone",
    "agent_decision",
    "agent_deferred",
]


class Checkpoint(StoreModel):
    """A snapshot of an agent's working state at a point in time.

    One checkpoint per trigger event. Ordering is by ``created_at``
    (append-only JSONL). The ``phase`` field is the workflow phase
    name the agent is currently running; ``historical=True`` means
    the phase has completed and this record is kept only for audit.
    """

    ticket_id: str
    phase: str
    author: str
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    # Narrative slots per doc 09 §Shape.
    description: str = ""
    completed: list[str] = Field(default_factory=list)
    position: str = ""
    plan: str = ""
    ruled_out: list[RuledOut] = Field(default_factory=list)
    deferred: list[DeferredItem] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)

    trigger: CheckpointTrigger

    # Phase-boundary pruning: flipped to True by ``mark_phase_historical``
    # when the phase's Handoff is accepted. Skipped by default queries.
    historical: bool = False


__all__ = [
    "Checkpoint",
    "CheckpointTrigger",
    "RuledOut",
]
