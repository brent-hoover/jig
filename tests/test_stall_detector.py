"""Unit tests for StallDetector — pure logic, no I/O."""
from __future__ import annotations

import pytest

from jig.stall_detector import StallDetector, StallThresholds


@pytest.fixture()
def det() -> StallDetector:
    return StallDetector(
        thresholds=StallThresholds(
            heartbeat_gap_seconds=10.0,
            agent_wall_time_seconds=60.0,
            unanswered_needs_info_seconds=30.0,
            poll_interval_seconds=1.0,
            cooldown_seconds=5.0,
        )
    )


class TestNoStall:
    def test_empty_returns_none(self, det: StallDetector) -> None:
        assert det.check(now=1000.0) is None

    def test_healthy_heartbeat_returns_none(self, det: StallDetector) -> None:
        det.record_agent_start("t1:dev")
        det.record_heartbeat("t1:dev")
        assert det.check(now=det.last_heartbeat_at + 5.0) is None  # type: ignore[operator]

    def test_needs_info_within_threshold_returns_none(self, det: StallDetector) -> None:
        det.record_needs_info("t1")
        assert det.check(now=det.needs_info_since["t1"] + 10.0) is None


class TestHeartbeatGap:
    def test_gap_over_threshold_returns_verdict(self, det: StallDetector) -> None:
        det.record_agent_start("t1:dev")
        det.record_heartbeat("t1:dev")
        verdict = det.check(now=det.last_heartbeat_at + 11.0)  # type: ignore[operator]
        assert verdict is not None
        assert verdict.signal == "heartbeat_gap"

    def test_no_verdict_without_in_flight_agent(self, det: StallDetector) -> None:
        # Heartbeat recorded but agent already done — gap shouldn't fire.
        det.record_agent_start("t1:dev")
        det.record_heartbeat("t1:dev")
        det.record_agent_done("t1:dev")
        verdict = det.check(now=det.last_heartbeat_at + 999.0)  # type: ignore[operator]
        assert verdict is None or verdict.signal != "heartbeat_gap"

    def test_detail_contains_agent_key(self, det: StallDetector) -> None:
        det.record_agent_start("t1:dev")
        det.record_heartbeat("t1:dev")
        verdict = det.check(now=det.last_heartbeat_at + 20.0)  # type: ignore[operator]
        assert verdict is not None
        assert "t1:dev" in verdict.detail


class TestWallTime:
    # ``record_agent_start`` resets ``last_heartbeat_at`` to ``time.monotonic()``
    # so a re-spawned agent gets a full grace period. These tests rewind
    # ``in_flight_agents`` synthetically to ``now - X``; they also need to
    # rewind ``last_heartbeat_at`` so the heartbeat-gap check (ordered before
    # wall_time) doesn't trip on the difference between real monotonic time
    # and the synthetic ``now``. Without this, the test only passes on hosts
    # whose ``time.monotonic()`` is greater than the synthetic ``now`` —
    # macOS with long uptime, but not fresh Ubuntu CI containers.

    def test_wall_time_over_threshold(self, det: StallDetector) -> None:
        now = 1000.0
        det.record_agent_start("t1:dev")
        # Manually set start time far in the past
        det.in_flight_agents["t1:dev"] = now - 61.0
        det.last_heartbeat_at = now  # fresh heartbeat — only wall_time should trip
        verdict = det.check(now=now)
        assert verdict is not None
        assert verdict.signal == "wall_time"

    def test_wall_time_within_threshold_returns_none(self, det: StallDetector) -> None:
        now = 1000.0
        det.record_agent_start("t1:dev")
        det.in_flight_agents["t1:dev"] = now - 30.0
        det.last_heartbeat_at = now
        assert det.check(now=now) is None

    def test_concurrent_agents_tracked_independently(self, det: StallDetector) -> None:
        now = 1000.0
        det.record_agent_start("t1:dev")
        det.record_agent_start("t2:dev")
        det.in_flight_agents["t1:dev"] = now - 30.0
        det.in_flight_agents["t2:dev"] = now - 70.0  # only t2 trips
        det.last_heartbeat_at = now
        verdict = det.check(now=now)
        assert verdict is not None
        assert verdict.signal == "wall_time"
        assert "t2:dev" in verdict.detail

    def test_done_agent_no_longer_trips_wall_time(self, det: StallDetector) -> None:
        now = 1000.0
        det.record_agent_start("t1:dev")
        det.in_flight_agents["t1:dev"] = now - 70.0
        det.last_heartbeat_at = now
        det.record_agent_done("t1:dev")
        assert det.check(now=now) is None


class TestNeedsInfo:
    def test_needs_info_over_threshold(self, det: StallDetector) -> None:
        now = 1000.0
        det.record_needs_info("t1")
        det.needs_info_since["t1"] = now - 31.0
        verdict = det.check(now=now)
        assert verdict is not None
        assert verdict.signal == "unanswered_needs_info"

    def test_clear_needs_info_suppresses_verdict(self, det: StallDetector) -> None:
        now = 1000.0
        det.record_needs_info("t1")
        det.needs_info_since["t1"] = now - 31.0
        det.clear_needs_info("t1")
        assert det.check(now=now) is None


class TestPriority:
    def test_heartbeat_gap_beats_wall_time(self, det: StallDetector) -> None:
        """heartbeat_gap is checked first — it's more actionable."""
        now = 1000.0
        det.record_agent_start("t1:dev")
        det.record_heartbeat("t1:dev")
        det.last_heartbeat_at = now - 20.0  # trips heartbeat_gap
        det.in_flight_agents["t1:dev"] = now - 70.0  # also trips wall_time
        verdict = det.check(now=now)
        assert verdict is not None
        assert verdict.signal == "heartbeat_gap"
