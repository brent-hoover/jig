"""Block 2 — fixture-env wiring through ``_run_agent_with_analytics``.

The reviewer flagged that ``build_fixture_env`` is callable but never
called from the real-mode spawn path. These tests assert that
``ctx.extra_env`` carries ``JIG_FIXTURE_MODE`` for both spike and non-
spike tickets, alongside any dev-env URLs the provisioning hook produces.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jig.dev_env.fixtures import FIXTURE_MODE_ENV_VAR, FixtureMode
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


class _FakeResult:
    status = "success"
    final_text = ""
    total_cost_usd = None
    tokens_in = None
    tokens_out = None


class _FakeCtx:
    role = "dev"
    extra_env: dict[str, str] | None = None

    def __init__(self, t: Ticket) -> None:
        self.ticket = t


@pytest.mark.asyncio
async def test_fixture_env_replay_only_for_non_spike_ticket(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Feature ticket (non-spike) → REPLAY_ONLY in extra_env."""
    _save_project(tmp_path)
    orch = Orchestrator(project_path=tmp_path)
    await orch.startup()
    try:
        captured: dict = {}

        async def _fake_run_agent(ctx, emitter=None):
            captured["extra_env"] = dict(getattr(ctx, "extra_env", None) or {})
            return _FakeResult()

        monkeypatch.setattr("jig.agent.run_agent", _fake_run_agent)

        ticket = Ticket(
            id="t-feature",
            work_type=WorkType.FEATURE,
            title="t",
            created_by="u",
            description=TICKET_AC_PLACEHOLDER,
        )
        await orch._run_agent_with_analytics(_FakeCtx(ticket))

        assert captured["extra_env"].get(FIXTURE_MODE_ENV_VAR) == (
            FixtureMode.REPLAY_ONLY.value
        )
    finally:
        await orch.shutdown()


@pytest.mark.asyncio
async def test_fixture_env_record_new_for_spike_ticket(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Spike ticket → RECORD_NEW in extra_env (the spike grows fixtures)."""
    _save_project(tmp_path)
    orch = Orchestrator(project_path=tmp_path)
    await orch.startup()
    try:
        captured: dict = {}

        async def _fake_run_agent(ctx, emitter=None):
            captured["extra_env"] = dict(getattr(ctx, "extra_env", None) or {})
            return _FakeResult()

        monkeypatch.setattr("jig.agent.run_agent", _fake_run_agent)

        ticket = Ticket(
            id="t-spike",
            work_type=WorkType.SPIKE,
            title="t",
            created_by="u",
            description=TICKET_AC_PLACEHOLDER,
        )
        await orch._run_agent_with_analytics(_FakeCtx(ticket))

        assert captured["extra_env"].get(FIXTURE_MODE_ENV_VAR) == (
            FixtureMode.RECORD_NEW.value
        )
    finally:
        await orch.shutdown()


@pytest.mark.asyncio
async def test_fixture_env_does_not_clobber_dev_env_urls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fixture mode merges; existing JIG_DEV_*_URL entries survive."""
    _save_project(tmp_path)
    orch = Orchestrator(project_path=tmp_path)
    await orch.startup()
    try:
        from jig import orchestrator as orchestrator_module

        captured: dict = {}

        async def _fake_run_agent(ctx, emitter=None):
            captured["extra_env"] = dict(getattr(ctx, "extra_env", None) or {})
            return _FakeResult()

        async def _fake_provision(
            project_path,
            *,
            agent_id,
            ticket_id,
            epic_id=None,
            registry=None,
        ):
            return {"JIG_DEV_MAIN_DB_URL": "postgresql://stub"}

        async def _fake_cleanup(*args, **kwargs):
            return None

        monkeypatch.setattr("jig.agent.run_agent", _fake_run_agent)
        monkeypatch.setattr(orchestrator_module, "provision_for_agent", _fake_provision)
        monkeypatch.setattr(orchestrator_module, "cleanup_for_agent", _fake_cleanup)

        ticket = Ticket(
            id="t-merge",
            work_type=WorkType.FEATURE,
            title="t",
            created_by="u",
            description=TICKET_AC_PLACEHOLDER,
        )
        await orch._run_agent_with_analytics(_FakeCtx(ticket))

        assert captured["extra_env"].get("JIG_DEV_MAIN_DB_URL") == "postgresql://stub"
        assert captured["extra_env"].get(FIXTURE_MODE_ENV_VAR) == (
            FixtureMode.REPLAY_ONLY.value
        )
    finally:
        await orch.shutdown()
