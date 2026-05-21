"""metrics.json schema (v1) per ``DESIGN.md``.

The dashboard ingests the highest ``schema_version`` it knows and
ignores newer fields it doesn't understand. Bump the version when
adding required fields; keep additive optional fields backward-
compatible.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


SCHEMA_VERSION = 1


class TicketMetrics(BaseModel):
    total: int = 0
    resolved: int = 0
    failed: int = 0
    needs_info_max_minutes: float = 0.0


class PhaseMetric(BaseModel):
    runs: int = 0
    retries: int = 0
    avg_turns: float = 0.0


class AgentMetrics(BaseModel):
    total_turns: int = 0
    total_cost_usd: float = 0.0
    total_tokens_in: int = 0
    total_tokens_out: int = 0


class OperatorQuestionMetrics(BaseModel):
    asked: int = 0
    verifiable_in_hindsight: int = 0
    product_scope: int = 0


StallSignal = Literal[
    "bus_silence",
    "heartbeat_gap",
    "unanswered_needs_info",
    "phase_oscillation",
    "wall_time",
    "daemon_dead",
]


class StallMetrics(BaseModel):
    detected: int = 0
    signal: StallSignal | None = None


Outcome = Literal["succeeded", "stalled", "failed"]


class RunMetrics(BaseModel):
    """Top-level metrics.json document for a single eval run."""

    schema_version: int = SCHEMA_VERSION
    run_id: str
    project: str

    outcome: Outcome
    outcome_reason: str = ""

    started_at: str
    ended_at: str
    duration_s: float = 0.0

    tickets: TicketMetrics = Field(default_factory=TicketMetrics)
    phases: dict[str, PhaseMetric] = Field(default_factory=dict)
    agents: AgentMetrics = Field(default_factory=AgentMetrics)
    operator_questions: OperatorQuestionMetrics = Field(
        default_factory=OperatorQuestionMetrics
    )
    stalls: StallMetrics = Field(default_factory=StallMetrics)

    merge_failures: int = 0
    review_blocks: int = 0

    watcher_version: str = "1.0.0"
    jig_commit: str = ""
    tags: list[str] = Field(default_factory=list)
