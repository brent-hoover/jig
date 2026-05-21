"""Pure stall-detection logic.

Consumes WS event envelopes from the daemon's typed broadcast and
tracks per-signal "last seen" timestamps. ``check()`` consults the
thresholds and returns a stall verdict (or None if everything is
healthy).

Kept free of I/O so it can be unit-tested cheaply: the driver
(``run.py``) feeds events in and acts on verdicts.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from jig.evals.watcher.heuristics import StallSignal, StallThresholds


@dataclass
class StallVerdict:
    signal: StallSignal
    seconds_since_last_event: float
    detail: str


@dataclass
class StallDetector:
    """State machine for live stall detection.

    Feed every WS message via :meth:`observe`. Call :meth:`check`
    periodically (the driver does this on a poll loop) to get a
    verdict back when thresholds trip.
    """

    thresholds: StallThresholds = field(default_factory=StallThresholds)

    started_at: float = field(default_factory=time.monotonic)
    last_bus_event_at: float = field(default_factory=time.monotonic)
    last_heartbeat_at: float | None = None
    # role → start_time for in-flight agents (cleared on agent_run /
    # ticket_completed for that role+ticket pair).
    in_flight_agents: dict[str, float] = field(default_factory=dict)
    # ticket_id → entered_needs_info_at
    needs_info_since: dict[str, float] = field(default_factory=dict)

    def observe(self, msg: dict) -> None:
        """Update internal state from one daemon WS event envelope.

        Tolerates heterogeneous shapes (snapshot vs event vs others).
        """
        now = time.monotonic()
        msg_type = msg.get("type")
        topic = msg.get("topic")
        kind = msg.get("kind") or ""
        data = msg.get("data") or {}

        # Any event at all counts as bus activity.
        self.last_bus_event_at = now

        if topic == "agents":
            if kind == "thinking":
                self.last_heartbeat_at = now
                role = data.get("role") or "agent"
                if data.get("active") is False:
                    self.in_flight_agents.pop(role, None)
                else:
                    self.in_flight_agents.setdefault(role, now)
            elif kind == "start":
                role = data.get("role") or "agent"
                self.in_flight_agents.setdefault(role, now)
            elif kind == "run":
                # agent_run = SDK ResultMessage post-mortem; agent finished.
                role = data.get("role") or "agent"
                self.in_flight_agents.pop(role, None)

        if topic == "tickets" and msg_type == "event" and isinstance(data, dict):
            ticket_id = data.get("id") or data.get("ticket_id")
            status = data.get("status")
            if ticket_id and status:
                if status == "needs_info":
                    self.needs_info_since.setdefault(ticket_id, now)
                else:
                    self.needs_info_since.pop(ticket_id, None)

        if msg_type == "snapshot" and topic == "tickets":
            for t in data or []:
                tid = t.get("id")
                if t.get("status") == "needs_info" and tid:
                    self.needs_info_since.setdefault(tid, now)

    def check(self, *, now: float | None = None) -> StallVerdict | None:
        """Inspect timestamps; return a verdict if any threshold tripped.

        Returns None when everything is within budget.
        Order matters: most-specific signals first so the verdict
        names the actual root cause when multiple thresholds trip.
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
                    detail=f"no heartbeat for {gap:.0f}s while in-flight: {roles}",
                )

        # Single agent run exceeded wall-time budget.
        for role, started in self.in_flight_agents.items():
            elapsed = now - started
            if elapsed > t.agent_wall_time_seconds:
                return StallVerdict(
                    signal="wall_time",
                    seconds_since_last_event=elapsed,
                    detail=f"{role} has been running {elapsed:.0f}s",
                )

        # Unanswered needs_info — operator never replied.
        for tid, entered in self.needs_info_since.items():
            elapsed = now - entered
            if elapsed > t.unanswered_needs_info_seconds:
                return StallVerdict(
                    signal="unanswered_needs_info",
                    seconds_since_last_event=elapsed,
                    detail=f"ticket {tid[:8]} in needs_info for {elapsed:.0f}s",
                )

        # Bus silence — strongest catch-all when nothing fits above.
        gap = now - self.last_bus_event_at
        if gap > t.bus_silence_seconds:
            return StallVerdict(
                signal="bus_silence",
                seconds_since_last_event=gap,
                detail=f"no bus events for {gap:.0f}s",
            )

        return None
