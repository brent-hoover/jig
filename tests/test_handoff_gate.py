"""Tests for jig.handoff_gate (Phase 5 Task O1a).

The primitive glues three existing pieces — the check runners, the
gate scorer, and the thread/ticket stores — into a single entry
point the orchestrator can call when a handoff is posted. These
tests only exercise scripted checks so the agent runner's branch
short-circuits (``run_for_phase`` skips non-matching types).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from jig.check_gate import CheckFailureEntry, GateVerdict
from jig.checks import CheckCatalog, CheckSeverity, ScriptedCheck
from jig.handoff_gate import (
    accept_handoff_automated,
    bounce_handoff,
    run_handoff_gate,
)
from jig.models import PhaseConfig, WorkflowConfig
from jig.store import MessageBus
from jig.store.check_results import CheckResultsStore
from jig.store.checkpoints import CheckpointStore
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.thread import Handoff, Note, SystemEvent
from jig.thread_mcp import ThreadError
from jig.ticket import Ticket, WorkType


def _worktree(tmp_path: Path) -> Path:
    root = tmp_path / "wt"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(
        [
            "git", "-c", "user.email=t@t", "-c", "user.name=t",
            "commit", "--allow-empty", "-qm", "init",
        ],
        cwd=root, check=True,
    )
    return root


async def _stores(
    tmp_path: Path,
) -> tuple[TicketStore, ThreadStore, CheckResultsStore]:
    tickets = TicketStore(tmp_path / "tickets.jsonl")
    await tickets.load()
    threads = ThreadStore(tmp_path / "comments.jsonl")
    await threads.load()
    results = CheckResultsStore(tmp_path / "check_results.jsonl")
    await results.load()
    return tickets, threads, results


async def _bus(tmp_path: Path) -> MessageBus:
    bus = MessageBus(tmp_path / "messages.jsonl")
    await bus.load()
    return bus


async def _seed_pending_handoff(
    tickets: TicketStore,
    threads: ThreadStore,
    *,
    phase: str = "implement",
    author: str = "dev",
    workflow_name: str = "default",
) -> tuple[str, str]:
    """Create a ticket + pending Handoff and return (ticket_id, handoff_id)."""
    tid = await tickets.create(
        Ticket(
            work_type=WorkType.FEATURE,
            title="t",
            created_by="orchestrator",
            workflow=workflow_name,
        )
    )
    hid = await threads.post(
        Handoff(
            ticket_id=tid,
            author=author,
            phase=phase,
            outputs=[],
            summary="ready for review",
        )
    )
    return tid, hid


def _wf(
    *,
    phases: list[PhaseConfig],
    name: str = "default",
) -> WorkflowConfig:
    return WorkflowConfig(name=name, phases=phases)


class TestPassingGate:
    async def test_all_required_pass(self, tmp_path: Path) -> None:
        tickets, threads, results = await _stores(tmp_path)
        tid, hid = await _seed_pending_handoff(tickets, threads)
        catalog = CheckCatalog.model_validate(
            {"unit": ScriptedCheck(type="scripted", command="true")}
        )
        workflow = _wf(
            phases=[
                PhaseConfig(
                    name="implement",
                    role="dev",
                    automated_checks=["unit"],
                )
            ]
        )

        verdict = await run_handoff_gate(
            handoff_id=hid,
            tickets=tickets,
            threads=threads,
            results=results,
            catalog=catalog,
            workflow=workflow,
            worktree_path=_worktree(tmp_path),
            project_path=tmp_path,
        )

        assert isinstance(verdict, GateVerdict)
        assert verdict.passing is True
        assert verdict.failing == []
        assert verdict.missing == []
        assert verdict.posted_events == []
        # CheckResult landed in the store.
        batch = await results.latest_batch(tid, "implement")
        names = {r.check_name for r in batch}
        assert names == {"unit"}

    async def test_no_automated_checks_declared(
        self, tmp_path: Path
    ) -> None:
        """Phase with empty automated_checks passes trivially; no
        runners fire."""
        tickets, threads, results = await _stores(tmp_path)
        _tid, hid = await _seed_pending_handoff(tickets, threads)
        catalog = CheckCatalog.model_validate({})
        workflow = _wf(
            phases=[PhaseConfig(name="implement", role="dev")]
        )

        verdict = await run_handoff_gate(
            handoff_id=hid,
            tickets=tickets,
            threads=threads,
            results=results,
            catalog=catalog,
            workflow=workflow,
            worktree_path=_worktree(tmp_path),
            project_path=tmp_path,
        )

        assert verdict.passing is True


class TestFailingGate:
    async def test_required_fail_blocks_and_posts_event(
        self, tmp_path: Path
    ) -> None:
        tickets, threads, results = await _stores(tmp_path)
        tid, hid = await _seed_pending_handoff(tickets, threads)
        catalog = CheckCatalog.model_validate(
            {"unit": ScriptedCheck(type="scripted", command="false")}
        )
        workflow = _wf(
            phases=[
                PhaseConfig(
                    name="implement",
                    role="dev",
                    automated_checks=["unit"],
                )
            ]
        )

        verdict = await run_handoff_gate(
            handoff_id=hid,
            tickets=tickets,
            threads=threads,
            results=results,
            catalog=catalog,
            workflow=workflow,
            worktree_path=_worktree(tmp_path),
            project_path=tmp_path,
        )

        assert verdict.passing is False
        assert [f.check_name for f in verdict.failing] == ["unit"]
        assert len(verdict.posted_events) == 1
        # The posted event is a check_failure SystemEvent on the ticket.
        entries = await threads.for_ticket(tid)
        failure_events = [
            e for e in entries
            if isinstance(e, SystemEvent)
            and e.event_type == "check_failure"
        ]
        assert len(failure_events) == 1
        assert failure_events[0].check_name == "unit"
        assert failure_events[0].waived is False


class TestWarningDoesntGate:
    async def test_warning_fail_still_passes_gate(
        self, tmp_path: Path
    ) -> None:
        tickets, threads, results = await _stores(tmp_path)
        _tid, hid = await _seed_pending_handoff(tickets, threads)
        catalog = CheckCatalog.model_validate(
            {
                "lint": ScriptedCheck(
                    type="scripted",
                    command="false",
                    severity=CheckSeverity.WARNING,
                ),
            }
        )
        workflow = _wf(
            phases=[
                PhaseConfig(
                    name="implement",
                    role="dev",
                    automated_checks=["lint"],
                )
            ]
        )

        verdict = await run_handoff_gate(
            handoff_id=hid,
            tickets=tickets,
            threads=threads,
            results=results,
            catalog=catalog,
            workflow=workflow,
            worktree_path=_worktree(tmp_path),
            project_path=tmp_path,
        )

        assert verdict.passing is True
        assert verdict.failing == []


class TestErrors:
    async def test_missing_handoff_raises_key_error(
        self, tmp_path: Path
    ) -> None:
        tickets, threads, results = await _stores(tmp_path)
        with pytest.raises(KeyError, match="handoff"):
            await run_handoff_gate(
                handoff_id="nope",
                tickets=tickets,
                threads=threads,
                results=results,
                catalog=CheckCatalog.model_validate({}),
                workflow=_wf(phases=[]),
                worktree_path=_worktree(tmp_path),
                project_path=tmp_path,
            )

    async def test_non_handoff_entry_raises_thread_error(
        self, tmp_path: Path
    ) -> None:
        tickets, threads, results = await _stores(tmp_path)
        tid = await tickets.create(
            Ticket(
                work_type=WorkType.FEATURE,
                title="t",
                created_by="orchestrator",
            )
        )
        nid = await threads.post(
            Note(ticket_id=tid, author="dev", text="hi")
        )
        with pytest.raises(ThreadError, match="not a handoff"):
            await run_handoff_gate(
                handoff_id=nid,
                tickets=tickets,
                threads=threads,
                results=results,
                catalog=CheckCatalog.model_validate({}),
                workflow=_wf(phases=[]),
                worktree_path=_worktree(tmp_path),
                project_path=tmp_path,
            )

    async def test_already_resolved_handoff_rejected(
        self, tmp_path: Path
    ) -> None:
        tickets, threads, results = await _stores(tmp_path)
        _tid, hid = await _seed_pending_handoff(tickets, threads)
        await threads.update(
            hid,
            {"acceptance_state": "accepted", "accepted_by": "reviewer"},
        )
        with pytest.raises(ThreadError, match="already"):
            await run_handoff_gate(
                handoff_id=hid,
                tickets=tickets,
                threads=threads,
                results=results,
                catalog=CheckCatalog.model_validate({}),
                workflow=_wf(
                    phases=[PhaseConfig(name="implement", role="dev")]
                ),
                worktree_path=_worktree(tmp_path),
                project_path=tmp_path,
            )

    async def test_unknown_phase_raises_key_error(
        self, tmp_path: Path
    ) -> None:
        tickets, threads, results = await _stores(tmp_path)
        _tid, hid = await _seed_pending_handoff(
            tickets, threads, phase="unknown"
        )
        with pytest.raises(KeyError, match="phase"):
            await run_handoff_gate(
                handoff_id=hid,
                tickets=tickets,
                threads=threads,
                results=results,
                catalog=CheckCatalog.model_validate({}),
                workflow=_wf(
                    phases=[PhaseConfig(name="implement", role="dev")]
                ),
                worktree_path=_worktree(tmp_path),
                project_path=tmp_path,
            )


class TestRunHandoffGateBusEvents:
    """Phase 5 Task O3 — when a ``bus`` is passed to
    ``run_handoff_gate``, the internal ScriptedRunner/AgentCheckRunner
    publish ``check_completed`` per result."""

    async def test_pass_publishes_check_completed(
        self, tmp_path: Path
    ) -> None:
        tickets, threads, results = await _stores(tmp_path)
        bus = await _bus(tmp_path)
        tid, hid = await _seed_pending_handoff(tickets, threads)
        catalog = CheckCatalog.model_validate(
            {"unit": ScriptedCheck(type="scripted", command="true")}
        )
        workflow = _wf(
            phases=[
                PhaseConfig(
                    name="implement",
                    role="dev",
                    automated_checks=["unit"],
                )
            ]
        )

        verdict = await run_handoff_gate(
            handoff_id=hid,
            tickets=tickets,
            threads=threads,
            results=results,
            catalog=catalog,
            workflow=workflow,
            worktree_path=_worktree(tmp_path),
            project_path=tmp_path,
            bus=bus,
        )

        assert verdict.passing is True
        msgs = await bus.get_history(f"tickets.{tid}")
        events = [
            m for m in msgs
            if m.payload.get("kind") == "check_completed"
        ]
        assert len(events) == 1
        assert events[0].payload["check_name"] == "unit"
        assert events[0].payload["verdict"] == "pass"


# ---- bounce_handoff -------------------------------------------------------


def _failing_verdict(
    *,
    failing: list[str] | None = None,
    missing: list[str] | None = None,
) -> GateVerdict:
    return GateVerdict(
        passing=False,
        failing=[
            CheckFailureEntry(
                check_name=n,
                verdict="fail",
                severity=CheckSeverity.REQUIRED,
                event_id=f"evt-{n}",
            )
            for n in (failing or [])
        ],
        missing=list(missing or []),
        posted_events=[f"evt-{n}" for n in (failing or [])],
        mode="handoff",
    )


class TestBounceHandoff:
    async def test_bounces_pending_handoff(self, tmp_path: Path) -> None:
        tickets, threads, _results = await _stores(tmp_path)
        bus = await _bus(tmp_path)
        tid, hid = await _seed_pending_handoff(tickets, threads)
        verdict = _failing_verdict(failing=["unit"])

        reason = await bounce_handoff(
            handoff_id=hid, threads=threads, bus=bus, verdict=verdict
        )

        assert "unit" in reason
        assert "evt-unit" in reason
        h = await threads.get(hid)
        assert isinstance(h, Handoff)
        assert h.acceptance_state == "rejected"
        assert h.rejection_reason == reason
        # Bus event has ticket topic and bounce=True flag.
        msgs = await bus.get_history(f"tickets.{tid}")
        match = [
            m for m in msgs
            if m.payload.get("kind") == "thread_handoff_rejected"
        ]
        assert len(match) == 1
        payload = match[0].payload
        assert payload["handoff_id"] == hid
        assert payload["bounce"] is True
        assert payload["rejected_by"] == "harness"
        assert payload["failing_checks"] == ["unit"]
        assert payload["missing_checks"] == []

    async def test_missing_checks_appear_in_reason(
        self, tmp_path: Path
    ) -> None:
        tickets, threads, _results = await _stores(tmp_path)
        bus = await _bus(tmp_path)
        _tid, hid = await _seed_pending_handoff(tickets, threads)
        verdict = _failing_verdict(missing=["lint", "typecheck"])

        reason = await bounce_handoff(
            handoff_id=hid, threads=threads, bus=bus, verdict=verdict
        )

        assert "lint" in reason
        assert "typecheck" in reason
        assert "no result" in reason

    async def test_already_resolved_raises(self, tmp_path: Path) -> None:
        tickets, threads, _results = await _stores(tmp_path)
        bus = await _bus(tmp_path)
        _tid, hid = await _seed_pending_handoff(tickets, threads)
        await threads.update(
            hid,
            {
                "acceptance_state": "rejected",
                "rejection_reason": "already rejected",
            },
        )
        with pytest.raises(ThreadError, match="already"):
            await bounce_handoff(
                handoff_id=hid,
                threads=threads,
                bus=bus,
                verdict=_failing_verdict(failing=["x"]),
            )

    async def test_missing_handoff_raises(self, tmp_path: Path) -> None:
        tickets, threads, _results = await _stores(tmp_path)
        bus = await _bus(tmp_path)
        with pytest.raises(KeyError, match="handoff"):
            await bounce_handoff(
                handoff_id="nope",
                threads=threads,
                bus=bus,
                verdict=_failing_verdict(failing=["x"]),
            )

    async def test_non_handoff_entry_raises(self, tmp_path: Path) -> None:
        tickets, threads, _results = await _stores(tmp_path)
        bus = await _bus(tmp_path)
        tid = await tickets.create(
            Ticket(
                work_type=WorkType.FEATURE,
                title="t",
                created_by="orchestrator",
            )
        )
        nid = await threads.post(
            Note(ticket_id=tid, author="dev", text="hi")
        )
        with pytest.raises(ThreadError, match="not a handoff"):
            await bounce_handoff(
                handoff_id=nid,
                threads=threads,
                bus=bus,
                verdict=_failing_verdict(failing=["x"]),
            )

    async def test_passing_verdict_refused(self, tmp_path: Path) -> None:
        """Sanity guard: you shouldn't be bouncing a passing gate."""
        tickets, threads, _results = await _stores(tmp_path)
        bus = await _bus(tmp_path)
        _tid, hid = await _seed_pending_handoff(tickets, threads)
        passing = GateVerdict(
            passing=True,
            failing=[],
            missing=[],
            posted_events=[],
            mode="handoff",
        )
        with pytest.raises(ThreadError, match="passing"):
            await bounce_handoff(
                handoff_id=hid,
                threads=threads,
                bus=bus,
                verdict=passing,
            )


# ---- accept_handoff_automated ---------------------------------------------


class TestAcceptHandoffAutomated:
    async def test_flips_handoff_to_accepted(self, tmp_path: Path) -> None:
        tickets, threads, _results = await _stores(tmp_path)
        bus = await _bus(tmp_path)
        tid, hid = await _seed_pending_handoff(tickets, threads)

        await accept_handoff_automated(
            handoff_id=hid, threads=threads, bus=bus
        )

        h = await threads.get(hid)
        assert isinstance(h, Handoff)
        assert h.acceptance_state == "accepted"
        assert h.accepted_by == "harness"
        # Bus event has ticket topic, auto=True flag.
        msgs = await bus.get_history(f"tickets.{tid}")
        match = [
            m for m in msgs
            if m.payload.get("kind") == "thread_handoff_accepted"
        ]
        assert len(match) == 1
        payload = match[0].payload
        assert payload["handoff_id"] == hid
        assert payload["auto"] is True
        assert payload["accepted_by"] == "harness"

    async def test_prunes_checkpoints_when_provided(
        self, tmp_path: Path
    ) -> None:
        """Parity with ``_close_handoff``'s accept branch — accepted
        phase's checkpoints move to historical so next-phase queries
        don't see them."""
        tickets, threads, _results = await _stores(tmp_path)
        bus = await _bus(tmp_path)
        tid, hid = await _seed_pending_handoff(tickets, threads)
        checkpoints = CheckpointStore(tmp_path / "checkpoints.jsonl")
        await checkpoints.load()

        from jig.checkpoints import Checkpoint
        cp_id = await checkpoints.post(
            Checkpoint(
                ticket_id=tid,
                phase="implement",
                author="dev",
                trigger="agent_milestone",
                description="ready",
            )
        )

        await accept_handoff_automated(
            handoff_id=hid,
            threads=threads,
            bus=bus,
            checkpoints=checkpoints,
        )

        cp = await checkpoints.get(cp_id)
        assert cp is not None
        assert cp.historical is True

    async def test_missing_handoff_raises(self, tmp_path: Path) -> None:
        _tickets, threads, _results = await _stores(tmp_path)
        bus = await _bus(tmp_path)
        with pytest.raises(KeyError, match="handoff"):
            await accept_handoff_automated(
                handoff_id="nope", threads=threads, bus=bus
            )

    async def test_already_resolved_raises(self, tmp_path: Path) -> None:
        tickets, threads, _results = await _stores(tmp_path)
        bus = await _bus(tmp_path)
        _tid, hid = await _seed_pending_handoff(tickets, threads)
        await threads.update(
            hid,
            {"acceptance_state": "accepted", "accepted_by": "reviewer"},
        )
        with pytest.raises(ThreadError, match="already"):
            await accept_handoff_automated(
                handoff_id=hid, threads=threads, bus=bus
            )

    async def test_non_handoff_entry_raises(self, tmp_path: Path) -> None:
        tickets, threads, _results = await _stores(tmp_path)
        bus = await _bus(tmp_path)
        tid = await tickets.create(
            Ticket(
                work_type=WorkType.FEATURE,
                title="t",
                created_by="orchestrator",
            )
        )
        nid = await threads.post(
            Note(ticket_id=tid, author="dev", text="hi")
        )
        with pytest.raises(ThreadError, match="not a handoff"):
            await accept_handoff_automated(
                handoff_id=nid, threads=threads, bus=bus
            )
