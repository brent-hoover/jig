"""Manifest schema for a single eval run."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TracerResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool
    exit_code: int
    stdout: str = ""
    stderr: str = ""


class RunManifest(BaseModel):
    """Captured metrics from one completed eval run.

    Written to evals/runs/<project-id>/<run-id>/manifest.yaml.
    All fields are read from the project's .jig/ stores after the run
    completes — nothing here is computed during the run itself.
    """

    model_config = ConfigDict(extra="forbid")

    # --- identity ---
    run_id: str
    project_id: str
    label: str | None = None
    collected_at: datetime
    git_sha: str | None = None
    jig_version: str | None = None

    # --- ticket outcomes ---
    ticket_status_counts: dict[str, int] = Field(default_factory=dict)

    # --- errors (SystemEvent counts by event_type) ---
    system_event_counts: dict[str, int] = Field(default_factory=dict)

    # --- reviewer findings ---
    reviewer_comment_counts: dict[str, int] = Field(default_factory=dict)

    # --- cost + throughput ---
    total_cost_usd: float = 0.0
    total_duration_ms: int = 0
    agent_spawn_count: int = 0
    fix_cycle_count: int = 0

    # --- tracer ---
    tracer: TracerResult | None = None

    # --- freeform extras for forward compat ---
    extra: dict[str, Any] = Field(default_factory=dict)
