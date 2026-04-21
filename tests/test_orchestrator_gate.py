"""Tests for orchestrator wiring of the handoff check gate (Phase 5 O1c).

``_run_handoff_gate_if_pending`` is the integration seam between the
orchestrator's per-ticket loop and ``jig.handoff_gate``. These tests
drive the method directly rather than running the full pipeline —
``_run_ticket`` covers the wider flow via the existing blocked-retry
branch once the handoff is flipped to ``rejected``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from jig.models import (
    AutomatedOnlyEvaluator,
    PhaseConfig,
    SpecificRoleEvaluator,
    WorkflowConfig,
)
from jig.orchestrator import Orchestrator
from jig.persistence import save_workflow
from jig.project import Project, save_project
from jig.thread import Handoff
from jig.ticket import Ticket, WorkType


def _init_git(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(
        [
            "git", "-c", "user.email=t@t", "-c", "user.name=t",
            "commit", "--allow-empty", "-qm", "init",
        ],
        cwd=path, check=True,
    )


def _write_check_catalog(project_path: Path, command: str) -> None:
    """Write ``.jig/checks.yaml`` with a single scripted 'unit' check."""
    checks_path = project_path / ".jig" / "checks.yaml"
    checks_path.parent.mkdir(parents=True, exist_ok=True)
    checks_path.write_text(
        yaml.safe_dump(
            {
                "checks": {
                    "unit": {"type": "scripted", "command": command},
                }
            }
        )
    )


def _project(tmp_path: Path) -> Path:
    save_project(
        tmp_path,
        Project(
            id="p", name="p", path=str(tmp_path),
            language="python", package_manager="uv",
        ),
    )
    _init_git(tmp_path)
    return tmp_path


def _phase(
    name: str = "implement",
    checks: list[str] | None = None,
    evaluator=None,
):
    return PhaseConfig(
        name=name,
        role="dev",
        automated_checks=list(checks or []),
        evaluator=evaluator,
    )


def _workflow(phases: list[PhaseConfig]) -> WorkflowConfig:
    return WorkflowConfig(name="default", phases=phases)


async def _seed_pending_handoff(
    orch: Orchestrator, *, phase: str = "implement"
) -> tuple[str, str]:
    assert orch.tickets is not None and orch.threads is not None
    tid = await orch.tickets.create(
        Ticket(
            work_type=WorkType.FEATURE,
            title="t",
            created_by="orchestrator",
        )
    )
    hid = await orch.threads.post(
        Handoff(
            ticket_id=tid,
            author="dev",
            phase=phase,
            outputs=[],
            summary="ready",
        )
    )
    return tid, hid


class TestNoPendingHandoff:
    @pytest.mark.asyncio
    async def test_no_handoff_is_noop(self, tmp_path: Path) -> None:
        project_path = _project(tmp_path)
        orch = Orchestrator(project_path=project_path)
        await orch.startup()
        try:
            assert orch.tickets is not None
            tid = await orch.tickets.create(
                Ticket(
                    work_type=WorkType.FEATURE, title="t",
                    created_by="orchestrator",
                )
            )
            # No handoff posted — method must return cleanly.
            await orch._run_handoff_gate_if_pending(
                tid,
                _phase(checks=["unit"]),
                _workflow([_phase(checks=["unit"])]),
                tmp_path,
            )
        finally:
            await orch.shutdown()

    @pytest.mark.asyncio
    async def test_resolved_handoff_ignored(self, tmp_path: Path) -> None:
        """A handoff that's already accepted/rejected is NOT re-gated."""
        project_path = _project(tmp_path)
        _write_check_catalog(project_path, "false")  # would fail if run
        orch = Orchestrator(project_path=project_path)
        await orch.startup()
        try:
            tid, hid = await _seed_pending_handoff(orch)
            assert orch.threads is not None
            await orch.threads.update(
                hid,
                {"acceptance_state": "accepted", "accepted_by": "reviewer"},
            )
            # If the gate ran, the 'false' check would flip it to rejected.
            await orch._run_handoff_gate_if_pending(
                tid,
                _phase(checks=["unit"]),
                _workflow([_phase(checks=["unit"])]),
                tmp_path,
            )
            h = await orch.threads.get(hid)
            assert isinstance(h, Handoff)
            assert h.acceptance_state == "accepted"
        finally:
            await orch.shutdown()


class TestPassingGate:
    @pytest.mark.asyncio
    async def test_passing_gate_leaves_handoff_pending(
        self, tmp_path: Path
    ) -> None:
        project_path = _project(tmp_path)
        _write_check_catalog(project_path, "true")
        orch = Orchestrator(project_path=project_path)
        await orch.startup()
        try:
            tid, hid = await _seed_pending_handoff(orch)
            await orch._run_handoff_gate_if_pending(
                tid,
                _phase(checks=["unit"]),
                _workflow([_phase(checks=["unit"])]),
                tmp_path,
            )
            assert orch.threads is not None
            h = await orch.threads.get(hid)
            assert isinstance(h, Handoff)
            assert h.acceptance_state == "pending"
            # Not rejected, so the retry path won't fire.
            assert not await orch._phase_handoff_rejected(tid, "implement")
            # CheckResult landed.
            assert orch.check_results is not None
            batch = await orch.check_results.latest_batch(tid, "implement")
            assert {r.check_name for r in batch} == {"unit"}
        finally:
            await orch.shutdown()


class TestFailingGateBounces:
    @pytest.mark.asyncio
    async def test_failing_gate_flips_handoff_to_rejected(
        self, tmp_path: Path
    ) -> None:
        project_path = _project(tmp_path)
        _write_check_catalog(project_path, "false")
        orch = Orchestrator(project_path=project_path)
        await orch.startup()
        try:
            tid, hid = await _seed_pending_handoff(orch)
            await orch._run_handoff_gate_if_pending(
                tid,
                _phase(checks=["unit"]),
                _workflow([_phase(checks=["unit"])]),
                tmp_path,
            )
            assert orch.threads is not None
            h = await orch.threads.get(hid)
            assert isinstance(h, Handoff)
            assert h.acceptance_state == "rejected"
            assert h.rejection_reason is not None
            assert "unit" in h.rejection_reason
            # Downstream retry gate picks this up.
            assert await orch._phase_handoff_rejected(tid, "implement")
        finally:
            await orch.shutdown()

    @pytest.mark.asyncio
    async def test_bounce_publishes_rejected_event(
        self, tmp_path: Path
    ) -> None:
        project_path = _project(tmp_path)
        _write_check_catalog(project_path, "false")
        orch = Orchestrator(project_path=project_path)
        await orch.startup()
        try:
            tid, hid = await _seed_pending_handoff(orch)
            await orch._run_handoff_gate_if_pending(
                tid,
                _phase(checks=["unit"]),
                _workflow([_phase(checks=["unit"])]),
                tmp_path,
            )
            assert orch.bus is not None
            msgs = await orch.bus.get_history(f"tickets.{tid}")
            rejected = [
                m for m in msgs
                if m.payload.get("kind") == "thread_handoff_rejected"
            ]
            assert len(rejected) == 1
            assert rejected[0].payload["bounce"] is True
            assert rejected[0].payload["failing_checks"] == ["unit"]
        finally:
            await orch.shutdown()


class TestCatalogLookup:
    @pytest.mark.asyncio
    async def test_reads_project_catalog_from_disk(
        self, tmp_path: Path
    ) -> None:
        """Sanity: the method reads ``.jig/checks.yaml`` via
        ``load_check_catalog`` at call time. Writing the catalog after
        startup still works because load happens per-call."""
        project_path = _project(tmp_path)
        orch = Orchestrator(project_path=project_path)
        await orch.startup()
        try:
            # Write the catalog AFTER startup to prove load is lazy.
            _write_check_catalog(project_path, "false")
            tid, hid = await _seed_pending_handoff(orch)
            await orch._run_handoff_gate_if_pending(
                tid,
                _phase(checks=["unit"]),
                _workflow([_phase(checks=["unit"])]),
                tmp_path,
            )
            assert orch.threads is not None
            h = await orch.threads.get(hid)
            assert isinstance(h, Handoff)
            assert h.acceptance_state == "rejected"
        finally:
            await orch.shutdown()


class TestAutomatedOnlyAutoAccepts:
    @pytest.mark.asyncio
    async def test_gate_pass_auto_accepts_automated_only_phase(
        self, tmp_path: Path
    ) -> None:
        """Phase evaluator=automated_only + gate pass → harness accepts."""
        project_path = _project(tmp_path)
        _write_check_catalog(project_path, "true")
        orch = Orchestrator(project_path=project_path)
        await orch.startup()
        try:
            tid, hid = await _seed_pending_handoff(orch)
            phase = _phase(checks=["unit"], evaluator=AutomatedOnlyEvaluator(type="automated_only"))
            await orch._run_handoff_gate_if_pending(
                tid, phase, _workflow([phase]), tmp_path,
            )
            assert orch.threads is not None
            h = await orch.threads.get(hid)
            assert isinstance(h, Handoff)
            assert h.acceptance_state == "accepted"
            assert h.accepted_by == "harness"
            # Bus event carries auto=True marker.
            assert orch.bus is not None
            msgs = await orch.bus.get_history(f"tickets.{tid}")
            accepted = [
                m for m in msgs
                if m.payload.get("kind") == "thread_handoff_accepted"
            ]
            assert len(accepted) == 1
            assert accepted[0].payload["auto"] is True
        finally:
            await orch.shutdown()

    @pytest.mark.asyncio
    async def test_gate_pass_leaves_pending_for_role_evaluator(
        self, tmp_path: Path
    ) -> None:
        """Phase with specific_role evaluator → handoff stays pending
        (O2b will spawn the evaluator)."""
        project_path = _project(tmp_path)
        _write_check_catalog(project_path, "true")
        orch = Orchestrator(project_path=project_path)
        await orch.startup()
        try:
            tid, hid = await _seed_pending_handoff(orch)
            phase = _phase(
                checks=["unit"],
                evaluator=SpecificRoleEvaluator(type="specific_role", role="reviewer"),
            )
            await orch._run_handoff_gate_if_pending(
                tid, phase, _workflow([phase]), tmp_path,
            )
            assert orch.threads is not None
            h = await orch.threads.get(hid)
            assert isinstance(h, Handoff)
            assert h.acceptance_state == "pending"
        finally:
            await orch.shutdown()

    @pytest.mark.asyncio
    async def test_gate_fail_does_not_auto_accept(
        self, tmp_path: Path
    ) -> None:
        """automated_only + gate fail must still bounce, not accept."""
        project_path = _project(tmp_path)
        _write_check_catalog(project_path, "false")
        orch = Orchestrator(project_path=project_path)
        await orch.startup()
        try:
            tid, hid = await _seed_pending_handoff(orch)
            phase = _phase(
                checks=["unit"], evaluator=AutomatedOnlyEvaluator(type="automated_only"),
            )
            await orch._run_handoff_gate_if_pending(
                tid, phase, _workflow([phase]), tmp_path,
            )
            assert orch.threads is not None
            h = await orch.threads.get(hid)
            assert isinstance(h, Handoff)
            assert h.acceptance_state == "rejected"
        finally:
            await orch.shutdown()


class TestNoChecksDeclared:
    @pytest.mark.asyncio
    async def test_phase_without_automated_checks_leaves_pending(
        self, tmp_path: Path
    ) -> None:
        project_path = _project(tmp_path)
        save_workflow(
            project_path, _workflow([_phase(checks=[])])
        )
        orch = Orchestrator(project_path=project_path)
        await orch.startup()
        try:
            tid, hid = await _seed_pending_handoff(orch)
            await orch._run_handoff_gate_if_pending(
                tid,
                _phase(checks=[]),
                _workflow([_phase(checks=[])]),
                tmp_path,
            )
            assert orch.threads is not None
            h = await orch.threads.get(hid)
            assert isinstance(h, Handoff)
            assert h.acceptance_state == "pending"
        finally:
            await orch.shutdown()
