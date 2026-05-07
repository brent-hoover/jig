"""Stall-detector thresholds + signal definitions (per DESIGN.md)."""
from __future__ import annotations

from dataclasses import dataclass

from evals.watcher.metrics import StallSignal  # re-export the Literal


@dataclass(frozen=True)
class StallThresholds:
    """All thresholds in seconds. Defaults match DESIGN.md guesses;
    tune from real data."""

    # No bus event published — strongest signal that the daemon is alive
    # but nothing's progressing.
    bus_silence_seconds: float = 300.0

    # No agent_thinking heartbeat while at least one agent is in flight.
    # Catches agents that hung mid-tool-call or mid-subprocess-teardown.
    heartbeat_gap_seconds: float = 90.0

    # A ticket sits in needs_info this long with no operator activity.
    # Operator unattended → run is effectively stalled.
    unanswered_needs_info_seconds: float = 1800.0

    # Single agent run wall time. Some legitimate agents are long
    # (planning, integration) so this is a generous safety net.
    agent_wall_time_seconds: float = 1800.0

    # How often the watcher re-checks its thresholds. Lower = faster
    # detection but more loop overhead.
    poll_interval_seconds: float = 5.0


__all__ = ["StallThresholds", "StallSignal"]
