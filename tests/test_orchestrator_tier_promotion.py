"""Orchestrator hook for mid-work tier promotion (Track F Final).

After the agent run finishes BLOCKED on an OPEN ticket and the
auto-escalation checker reports a tripped signal, the orchestrator
promotes the ticket's ``dev_tier`` so the next dispatch picks up at
the higher tier. Conservative: no mid-stream kill — the next dispatch
sees the new tier.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jig.analytics.events import AutoEscalationTriggered, PerCommitCheckFailed
from jig.orchestrator import Orchestrator
from jig.project import Project, save_project
from jig.ticket import Ticket, WorkType
from tests._test_ticket import TICKET_AC_PLACEHOLDER


def _save_project(tmp_path: Path) -> None:
    save_project(
        tmp_path,
        Project(
            id="p",
            name="p",
            path=str(tmp_path),
            language="python",
            package_manager="uv",
        ),
    )


@pytest.mark.asyncio
async def test_blocked_with_signal_promotes_standard_to_senior(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """3 PerCommitCheckFailed on the same contract → standard → senior."""
    _save_project(tmp_path)
    orch = Orchestrator(project_path=tmp_path)
    await orch.startup()
    try:
        from jig import orchestrator as orchestrator_module

        class _FakeResult:
            status = "blocked"
            final_text = ""
            total_cost_usd = 0.01
            tokens_in = 100
            tokens_out = 50

        async def _fake_run_agent(ctx, emitter=None):
            return _FakeResult()

        monkeypatch.setattr(orchestrator_module, "run_agent", _fake_run_agent)

        ticket = Ticket(
            id="tb-cat",
            work_type=WorkType.FEATURE,
            title="t",
            created_by="u",
            dev_tier="standard",
            description=TICKET_AC_PLACEHOLDER,
        )
        assert orch.tickets is not None and orch.analytics is not None
        await orch.tickets.create(ticket)

        # Pre-seed three failed per-commit checks on the same contract,
        # so the auto-escalation checker reports a tripped signal.
        for sha in ("a", "b", "c"):
            await orch.analytics.append(
                PerCommitCheckFailed(
                    ticket_id="tb-cat",
                    agent_id="dev:tb-cat",
                    commit_sha=sha,
                    reviewer_role="contract_compliance",
                    violation_category="ownership",
                    contract_uri="project://arch/modules/foo/contracts#owns/x",
                    severity="critical",
                    auto_applied=False,
                )
            )

        class _FakeCtx:
            role = "dev"

            def __init__(self, t: Ticket) -> None:
                self.ticket = t

        await orch._run_agent_with_analytics(_FakeCtx(ticket))
        await orch._analytics_emitter.drain()

        refreshed = await orch.tickets.get("tb-cat")
        assert refreshed is not None
        assert refreshed.dev_tier == "senior"

        # Promotion fires AutoEscalationTriggered.
        events = await orch.analytics.by_kind("auto_escalation_triggered")
        assert len(events) == 1
        ev = events[0]
        assert isinstance(ev, AutoEscalationTriggered)
        assert ev.from_tier == "standard"
        assert ev.to_tier == "senior"
        assert ev.trip_signal == "repeated_same_failure"
    finally:
        await orch.shutdown()


@pytest.mark.asyncio
async def test_success_status_does_not_promote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Successful runs never auto-promote, even with tripped signals."""
    _save_project(tmp_path)
    orch = Orchestrator(project_path=tmp_path)
    await orch.startup()
    try:
        from jig import orchestrator as orchestrator_module

        class _FakeResult:
            status = "success"
            final_text = "ok"
            total_cost_usd = 0.0
            tokens_in = 0
            tokens_out = 0

        async def _fake_run_agent(ctx, emitter=None):
            return _FakeResult()

        monkeypatch.setattr(orchestrator_module, "run_agent", _fake_run_agent)

        ticket = Ticket(
            id="tb-cat",
            work_type=WorkType.FEATURE,
            title="t",
            created_by="u",
            dev_tier="standard",
            description=TICKET_AC_PLACEHOLDER,
        )
        assert orch.tickets is not None and orch.analytics is not None
        await orch.tickets.create(ticket)

        # Even with three failures stored, success status skips promotion.
        for sha in ("a", "b", "c"):
            await orch.analytics.append(
                PerCommitCheckFailed(
                    ticket_id="tb-cat",
                    agent_id="dev:tb-cat",
                    commit_sha=sha,
                    reviewer_role="contract_compliance",
                    violation_category="ownership",
                    contract_uri="project://arch/modules/foo/contracts#owns/x",
                    severity="critical",
                    auto_applied=False,
                )
            )

        class _FakeCtx:
            role = "dev"

            def __init__(self, t: Ticket) -> None:
                self.ticket = t

        await orch._run_agent_with_analytics(_FakeCtx(ticket))
        await orch._analytics_emitter.drain()

        refreshed = await orch.tickets.get("tb-cat")
        assert refreshed is not None
        assert refreshed.dev_tier == "standard"
    finally:
        await orch.shutdown()


@pytest.mark.asyncio
async def test_blocked_without_signals_does_not_promote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BLOCKED with no tripped signal leaves the tier alone."""
    _save_project(tmp_path)
    orch = Orchestrator(project_path=tmp_path)
    await orch.startup()
    try:
        from jig import orchestrator as orchestrator_module

        class _FakeResult:
            status = "blocked"
            final_text = ""
            total_cost_usd = 0.0
            tokens_in = 0
            tokens_out = 0

        async def _fake_run_agent(ctx, emitter=None):
            return _FakeResult()

        monkeypatch.setattr(orchestrator_module, "run_agent", _fake_run_agent)

        ticket = Ticket(
            id="tb-cat",
            work_type=WorkType.FEATURE,
            title="t",
            created_by="u",
            dev_tier="standard",
            description=TICKET_AC_PLACEHOLDER,
        )
        assert orch.tickets is not None and orch.analytics is not None
        await orch.tickets.create(ticket)

        class _FakeCtx:
            role = "dev"

            def __init__(self, t: Ticket) -> None:
                self.ticket = t

        await orch._run_agent_with_analytics(_FakeCtx(ticket))
        await orch._analytics_emitter.drain()

        refreshed = await orch.tickets.get("tb-cat")
        assert refreshed is not None
        assert refreshed.dev_tier == "standard"
    finally:
        await orch.shutdown()


@pytest.mark.asyncio
async def test_sa_tier_no_further_promotion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SA-tier ticket tripping signals doesn't auto-promote (operator owns it)."""
    _save_project(tmp_path)
    orch = Orchestrator(project_path=tmp_path)
    await orch.startup()
    try:
        from jig import orchestrator as orchestrator_module

        class _FakeResult:
            status = "blocked"
            final_text = ""
            total_cost_usd = 0.0
            tokens_in = 0
            tokens_out = 0

        async def _fake_run_agent(ctx, emitter=None):
            return _FakeResult()

        monkeypatch.setattr(orchestrator_module, "run_agent", _fake_run_agent)

        ticket = Ticket(
            id="tb-cat",
            work_type=WorkType.FEATURE,
            title="t",
            created_by="u",
            dev_tier="sa",
            description=TICKET_AC_PLACEHOLDER,
        )
        assert orch.tickets is not None and orch.analytics is not None
        await orch.tickets.create(ticket)
        for sha in ("a", "b", "c"):
            await orch.analytics.append(
                PerCommitCheckFailed(
                    ticket_id="tb-cat",
                    agent_id="dev:tb-cat",
                    commit_sha=sha,
                    reviewer_role="contract_compliance",
                    violation_category="ownership",
                    contract_uri="project://arch/modules/foo/contracts#owns/x",
                    severity="critical",
                    auto_applied=False,
                )
            )

        class _FakeCtx:
            role = "dev"

            def __init__(self, t: Ticket) -> None:
                self.ticket = t

        await orch._run_agent_with_analytics(_FakeCtx(ticket))
        await orch._analytics_emitter.drain()

        refreshed = await orch.tickets.get("tb-cat")
        assert refreshed is not None
        assert refreshed.dev_tier == "sa"
    finally:
        await orch.shutdown()
