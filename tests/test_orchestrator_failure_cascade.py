"""Failed-ticket cascade + stuck watchdog (review-severity-binary, step 2).

A ticket is only scheduled when ALL dependencies are resolved, so a
FAILED dependency makes its dependents permanently unreachable. Before
this feature they sat ``open`` forever: ``_maybe_run_analyzer`` never
saw an all-terminal project, ``project_complete`` never fired, and the
run idled silently until an external stall detector killed it (the
2026-06-12 hn-cli eval).

Pins, per feature-work/review-severity-binary/plan.md step 2:

- transitive cascade: dependents (and their dependents) of a failed
  ticket reach FAILED with ``block_reason="dependency-failed"``;
- ``project_complete`` fires with accurate resolved/failed counts and
  the analyzer task is started;
- a ticket created *after* its dependency failed cascades at schedule
  time;
- review-notable proposed issues do not hold ``project_complete``
  hostage (plain proposed front-door issues still do);
- the stuck watchdog emits ``project_stuck`` once per distinct
  dead-end set, only after the grace period, and never while work is
  running, ready, or operator-pending.
"""

from __future__ import annotations

import time
from pathlib import Path


from jig.config import Config, OrchestratorSection, save_config
from jig.events import EventEmitter, JigEvent
from jig.orchestrator import (
    DEP_FAILED_REASON,
    STUCK_GRACE_SECONDS,
    Orchestrator,
)
from jig.project import Project
from jig.store import MessageBus
from jig.store.tickets import TicketStore
from jig.store.threads import ThreadStore
from jig.ticket import Ticket, TicketStatus, WorkType
from tests._test_ticket import TICKET_AC_PLACEHOLDER


def _seed_project(root: Path) -> None:
    cfg_dir = root / ".jig"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg = Config(
        project=Project(id="test-cascade", name="test-cascade", path=str(root)),
        orchestrator=OrchestratorSection(),
    )
    save_config(root, cfg)


async def _make_orch(tmp_path: Path) -> tuple[Orchestrator, list[JigEvent]]:
    """Orchestrator with hand-wired stores + an event sink.

    ``_run_analyzer_bg`` is stubbed to a no-op that records invocation —
    these tests pin the trigger and the ``project_complete`` payload,
    not the analyzer itself.
    """
    _seed_project(tmp_path)
    store_dir = tmp_path / ".jig" / "store"
    store_dir.mkdir(parents=True, exist_ok=True)
    orch = Orchestrator(project_path=tmp_path)
    orch.tickets = TicketStore(store_dir / "tickets.jsonl")
    orch.threads = ThreadStore(store_dir / "comments.jsonl")
    orch.bus = MessageBus(store_dir / "messages.jsonl")
    await orch.tickets.load()
    await orch.threads.load()
    await orch.bus.load()

    events: list[JigEvent] = []

    class _SinkEmitter(EventEmitter):
        async def emit(self, event: JigEvent) -> None:  # type: ignore[override]
            events.append(event)
            await super().emit(event)

    orch._emitter = _SinkEmitter()

    async def _no_analyzer() -> None:
        events.append(JigEvent(type="_analyzer_invoked", data={}))

    orch._run_analyzer_bg = _no_analyzer  # type: ignore[method-assign]
    return orch, events


async def _ticket(
    orch: Orchestrator,
    ticket_id: str,
    *,
    status: TicketStatus = TicketStatus.OPEN,
    blocks: list[str] | None = None,
    blocked_by: list[str] | None = None,
    labels: list[str] | None = None,
) -> Ticket:
    assert orch.tickets is not None
    t = Ticket(
        id=ticket_id,
        work_type=WorkType.FEATURE,
        title=f"ticket {ticket_id}",
        created_by="planner-pm",
        description=TICKET_AC_PLACEHOLDER,
        status=status,
        blocks=blocks or [],
        blocked_by=blocked_by or [],
        labels=labels or [],
    )
    await orch.tickets.create(t)
    fresh = await orch.tickets.get(ticket_id)
    assert fresh is not None
    return fresh


def _events_of(events: list[JigEvent], type_: str) -> list[JigEvent]:
    return [e for e in events if e.type == type_]


class TestCascade:
    async def test_transitive_cascade_and_project_complete(
        self, tmp_path: Path
    ) -> None:
        """A fails → B (depends on A) and C (depends on B) cascade to
        FAILED(dependency-failed) → project_complete fires with accurate
        counts → analyzer starts."""
        orch, events = await _make_orch(tmp_path)
        assert orch.tickets is not None
        a = await _ticket(orch, "a", blocks=["b"])
        await _ticket(orch, "b", blocked_by=["a"], blocks=["c"])
        await _ticket(orch, "c", blocked_by=["b"])
        await _ticket(orch, "d", status=TicketStatus.RESOLVED)

        await orch.tickets.update_status("a", TicketStatus.FAILED)
        await orch._on_ticket_failed("a", a)

        for tid in ("b", "c"):
            t = await orch.tickets.get(tid)
            assert t is not None
            assert t.status == TicketStatus.FAILED
            assert t.block_reason == DEP_FAILED_REASON

        completes = _events_of(events, "project_complete")
        assert len(completes) == 1
        payload = completes[0].data
        assert payload["tickets_resolved"] == 1
        assert payload["tickets_failed"] == 3
        assert payload["tickets_total"] == 4
        # Each cascaded ticket announced its own failure.
        failed_ids = {e.data["ticket_id"] for e in _events_of(events, "ticket_failed")}
        assert failed_ids == {"a", "b", "c"}
        assert orch._analyzer_task is not None
        await orch._analyzer_task
        assert _events_of(events, "_analyzer_invoked")

    async def test_cascade_skips_non_open_dependents(self, tmp_path: Path) -> None:
        orch, _events = await _make_orch(tmp_path)
        assert orch.tickets is not None
        a = await _ticket(orch, "a", blocks=["b"])
        await _ticket(orch, "b", blocked_by=["a"], status=TicketStatus.RESOLVED)

        await orch.tickets.update_status("a", TicketStatus.FAILED)
        await orch._on_ticket_failed("a", a)

        b = await orch.tickets.get("b")
        assert b is not None
        assert b.status == TicketStatus.RESOLVED
        assert b.block_reason is None

    async def test_late_ticket_with_failed_dep_cascades_at_schedule(
        self, tmp_path: Path
    ) -> None:
        """A ticket created after its dependency already failed is
        cascade-failed the moment scheduling considers it."""
        orch, events = await _make_orch(tmp_path)
        assert orch.tickets is not None
        await _ticket(orch, "a", status=TicketStatus.FAILED)
        await _ticket(orch, "e", blocked_by=["a"])

        await orch._handle_schedule("e")

        e = await orch.tickets.get("e")
        assert e is not None
        assert e.status == TicketStatus.FAILED
        assert e.block_reason == DEP_FAILED_REASON
        assert _events_of(events, "ticket_failed")
        # And the now-all-terminal project completes.
        assert _events_of(events, "project_complete")

    async def test_sweep_fails_late_ticket_with_failed_dep(
        self, tmp_path: Path
    ) -> None:
        """An OPEN ticket blocked by an already-FAILED dependency — never
        scheduled, so find_ready/_handle_schedule never see it — is
        cascade-failed by the reconcile sweep, and the project completes."""
        orch, events = await _make_orch(tmp_path)
        assert orch.tickets is not None
        await _ticket(orch, "a", status=TicketStatus.FAILED)
        # Created after a failed; blocked_by a, and itself blocks c.
        await _ticket(orch, "late", blocked_by=["a"], blocks=["c"])
        await _ticket(orch, "c", blocked_by=["late"])

        await orch._sweep_failed_dependencies()

        for tid in ("late", "c"):
            t = await orch.tickets.get(tid)
            assert t is not None
            assert t.status == TicketStatus.FAILED
            assert t.block_reason == DEP_FAILED_REASON
        # All terminal now → project_complete fired.
        assert _events_of(events, "project_complete")
        # Downstream dependent (c) attributes blame to the original failed
        # dependency (a), not the intermediate swept ticket (late).
        cascade_events = [
            e
            for e in events
            if e.type == "ticket_failed" and e.data["ticket_id"] == "c"
        ]
        assert cascade_events

    async def test_sweep_noop_when_deps_not_failed(self, tmp_path: Path) -> None:
        orch, events = await _make_orch(tmp_path)
        assert orch.tickets is not None
        await _ticket(orch, "a", status=TicketStatus.RESOLVED)
        await _ticket(orch, "b", blocked_by=["a"])  # dep resolved, not failed

        await orch._sweep_failed_dependencies()

        b = await orch.tickets.get("b")
        assert b is not None
        assert b.status == TicketStatus.OPEN  # untouched

    async def test_sweep_blame_attributes_to_original_dep(self, tmp_path: Path) -> None:
        """A swept late ticket's downstream dependents attribute
        TicketCascadeFailed.root_failure_id to the original failed
        dependency, not the intermediate swept ticket (roborev job 562)."""
        orch, _events = await _make_orch(tmp_path)
        assert orch.tickets is not None

        emitted: list = []

        class _Emitter:
            def emit_nowait(self, event) -> None:
                emitted.append(event)

        orch._analytics_emitter = _Emitter()  # type: ignore[assignment]

        await _ticket(orch, "a", status=TicketStatus.FAILED)
        await _ticket(orch, "late", blocked_by=["a"], blocks=["c"])
        await _ticket(orch, "c", blocked_by=["late"])

        await orch._sweep_failed_dependencies()

        cascades = {
            e.ticket_id: e.root_failure_id
            for e in emitted
            if e.kind == "ticket_cascade_failed"
        }
        # both the late ticket and its downstream dependent blame "a".
        assert cascades.get("late") == "a"
        assert cascades.get("c") == "a"


class TestCompletionGating:
    async def test_review_notable_proposed_does_not_block_completion(
        self, tmp_path: Path
    ) -> None:
        orch, events = await _make_orch(tmp_path)
        await _ticket(orch, "a", status=TicketStatus.RESOLVED)
        await _ticket(
            orch,
            "nb",
            status=TicketStatus.PROPOSED,
            labels=["review-notable", "sig:abc123"],
        )

        await orch._maybe_run_analyzer()

        completes = _events_of(events, "project_complete")
        assert len(completes) == 1
        assert completes[0].data["tickets_resolved"] == 1

    async def test_plain_proposed_issue_still_blocks_completion(
        self, tmp_path: Path
    ) -> None:
        orch, events = await _make_orch(tmp_path)
        await _ticket(orch, "a", status=TicketStatus.RESOLVED)
        await _ticket(orch, "fd", status=TicketStatus.PROPOSED)

        await orch._maybe_run_analyzer()

        assert not _events_of(events, "project_complete")


class TestStuckWatchdog:
    async def _stuck_pair(self, orch: Orchestrator) -> None:
        """Dependency cycle: neither ticket can ever become ready, and
        the cascade can't see it (nothing FAILED)."""
        await _ticket(orch, "x", blocked_by=["y"], blocks=["y"])
        await _ticket(orch, "y", blocked_by=["x"], blocks=["x"])

    async def test_emits_once_after_grace(self, tmp_path: Path) -> None:
        orch, events = await _make_orch(tmp_path)
        await self._stuck_pair(orch)

        await orch._check_stuck()  # stamps _stuck_since
        assert not _events_of(events, "project_stuck")

        orch._stuck_since = time.monotonic() - (STUCK_GRACE_SECONDS + 1)
        await orch._check_stuck()
        stuck = _events_of(events, "project_stuck")
        assert len(stuck) == 1
        assert set(stuck[0].data["stuck_tickets"]) == {"x", "y"}

        # Same stuck set on a later tick → no duplicate event.
        await orch._check_stuck()
        assert len(_events_of(events, "project_stuck")) == 1

    async def test_quiet_while_needs_info_pending(self, tmp_path: Path) -> None:
        """Operator-pending tickets are quiet-but-alive, not stuck."""
        orch, events = await _make_orch(tmp_path)
        await self._stuck_pair(orch)
        await _ticket(orch, "q", status=TicketStatus.NEEDS_INFO)

        await orch._check_stuck()
        orch._stuck_since = time.monotonic() - (STUCK_GRACE_SECONDS + 1)
        await orch._check_stuck()

        assert not _events_of(events, "project_stuck")
        assert orch._stuck_since is None  # condition resets, not paused

    async def test_resets_when_work_becomes_ready(self, tmp_path: Path) -> None:
        orch, events = await _make_orch(tmp_path)
        await self._stuck_pair(orch)
        await orch._check_stuck()
        assert orch._stuck_since is not None

        # A ready ticket appears — the project is alive again.
        await _ticket(orch, "r")
        await orch._check_stuck()
        assert orch._stuck_since is None
        assert not _events_of(events, "project_stuck")
