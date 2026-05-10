"""Pure stall-detection logic for the jig daemon.

Tracks per-signal "last seen" timestamps from live orchestrator state.
``check()`` returns a StallVerdict when any threshold trips, or None
when everything is healthy.

Kept free of I/O so it is unit-testable: callers feed state in and
act on verdicts.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal


StallSignal = Literal[
    "heartbeat_gap",
    "wall_time",
    "unanswered_needs_info",
]


@dataclass(frozen=True)
class StallThresholds:
    """All thresholds in seconds."""

    # No agent_thinking heartbeat while at least one agent is in flight.
    heartbeat_gap_seconds: float = 90.0

    # Single agent run wall time.
    agent_wall_time_seconds: float = 1800.0

    # Ticket sits in needs_info with no response.
    unanswered_needs_info_seconds: float = 1800.0

    # Poll interval for the background check loop.
    poll_interval_seconds: float = 10.0

    # After firing a stall action, wait this long before firing again.
    cooldown_seconds: float = 120.0


@dataclass
class StallVerdict:
    signal: StallSignal
    seconds_since_last_event: float
    detail: str


@dataclass
class StallDetector:
    """State machine driven directly by orchestrator-internal state.

    Unlike the eval-watcher version this one receives typed updates
    rather than raw WS envelopes, since it runs inside the daemon.
    """

    thresholds: StallThresholds = field(default_factory=StallThresholds)

    # Updated on every agent_thinking pulse via on_thinking callback.
    last_heartbeat_at: float | None = None
    # "ticket_id:role" → monotonic start time (set on agent spawn, cleared on finish).
    # Keyed by ticket_id:role so concurrent agents sharing a role don't collide.
    in_flight_agents: dict[str, float] = field(default_factory=dict)
    # ticket_id → monotonic time when ticket entered needs_info.
    needs_info_since: dict[str, float] = field(default_factory=dict)

    def record_heartbeat(self, agent_key: str) -> None:
        """Call on every agent_thinking event. agent_key = 'ticket_id:role'."""
        now = time.monotonic()
        self.last_heartbeat_at = now
        self.in_flight_agents.setdefault(agent_key, now)

    def record_agent_start(self, agent_key: str) -> None:
        self.in_flight_agents.setdefault(agent_key, time.monotonic())

    def record_agent_done(self, agent_key: str) -> None:
        self.in_flight_agents.pop(agent_key, None)

    def record_needs_info(self, ticket_id: str) -> None:
        self.needs_info_since.setdefault(ticket_id, time.monotonic())

    def clear_needs_info(self, ticket_id: str) -> None:
        self.needs_info_since.pop(ticket_id, None)

    def check(self, *, now: float | None = None) -> StallVerdict | None:
        """Return a verdict if any threshold tripped, else None.

        Order: most actionable signal first.
        """
        now = now if now is not None else time.monotonic()
        t = self.thresholds

        # Heartbeat gap — only meaningful while an agent is in flight.
        if self.in_flight_agents and self.last_heartbeat_at is not None:
            gap = now - self.last_heartbeat_at
            if gap > t.heartbeat_gap_seconds:
                roles = ", ".join(sorted(self.in_flight_agents))
                return StallVerdict(
                    signal="heartbeat_gap",
                    seconds_since_last_event=gap,
                    detail=f"no heartbeat for {gap:.0f}s; in-flight: {roles}",
                )

        # Wall time — any single agent running too long.
        for role, started in self.in_flight_agents.items():
            elapsed = now - started
            if elapsed > t.agent_wall_time_seconds:
                return StallVerdict(
                    signal="wall_time",
                    seconds_since_last_event=elapsed,
                    detail=f"{role} has been running {elapsed:.0f}s",
                )

        # Unanswered needs_info.
        for tid, entered in self.needs_info_since.items():
            elapsed = now - entered
            if elapsed > t.unanswered_needs_info_seconds:
                return StallVerdict(
                    signal="unanswered_needs_info",
                    seconds_since_last_event=elapsed,
                    detail=f"ticket {tid[:8]} in needs_info for {elapsed:.0f}s",
                )

        return None
