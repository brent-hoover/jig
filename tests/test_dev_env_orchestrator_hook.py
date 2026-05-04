"""Tests for the orchestrator-side dev-env hook (Track E MVP)."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from jig.dev_env.manifest import derive_manifest
from jig.dev_env.orchestrator_hook import (
    ENV_VAR_PREFIX,
    build_env_var_name,
    cleanup_for_agent,
    load_manifest_or_none,
    provision_for_agent,
)
from jig.dev_env.provisioning import (
    ProvisioningRegistry,
    PostgresSchemaProvisioner,
)
from jig.intent import Intent
from jig.orchestrator import Orchestrator
from jig.project import Project, save_project
from jig.schemas.arch import (
    Architecture,
    DataStore,
    DevProvisioning,
    Module,
)
from jig.spec_loader import save_architecture, save_dev_manifest
from jig.ticket import Ticket, WorkType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _intent(p: str = "P", s: str = "S") -> Intent:
    return Intent(problem=p, simplest_solution=s)


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


def _seed_arch_and_manifest(tmp_path: Path) -> None:
    arch = Architecture(
        data_stores=[
            DataStore(
                id="main-db",
                kind="postgres",
                dev_provisioning=DevProvisioning(
                    strategy="shared_namespaced",
                    namespace_template="agent_{ticket_id}",
                ),
            ),
        ],
        modules=[
            Module(
                id="catalog-ingest",
                title="t",
                summary="s",
                intent=_intent(),
            )
        ],
    )
    save_architecture(tmp_path, arch)
    save_dev_manifest(tmp_path, derive_manifest(arch))


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def __call__(self, sql: str) -> None:
        self.calls.append(sql)


# ---------------------------------------------------------------------------
# build_env_var_name
# ---------------------------------------------------------------------------


def test_build_env_var_name_uppercases_and_sanitizes():
    assert build_env_var_name("main-db") == "JIG_DEV_MAIN_DB_URL"
    assert build_env_var_name("event.bus") == "JIG_DEV_EVENT_BUS_URL"
    assert build_env_var_name("simple") == "JIG_DEV_SIMPLE_URL"
    assert build_env_var_name("x").startswith(ENV_VAR_PREFIX)


# ---------------------------------------------------------------------------
# load_manifest_or_none — absence path
# ---------------------------------------------------------------------------


def test_load_manifest_or_none_returns_none_when_absent(tmp_path: Path):
    assert load_manifest_or_none(tmp_path) is None


def test_load_manifest_or_none_returns_manifest_when_present(tmp_path: Path):
    _seed_arch_and_manifest(tmp_path)
    m = load_manifest_or_none(tmp_path)
    assert m is not None
    assert [s.id for s in m.services] == ["main-db"]


# ---------------------------------------------------------------------------
# provision_for_agent / cleanup_for_agent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provision_for_agent_returns_empty_when_no_manifest(
    tmp_path: Path,
):
    out = await provision_for_agent(
        tmp_path, agent_id="a", ticket_id="t-1"
    )
    assert out == {}


@pytest.mark.asyncio
async def test_provision_for_agent_returns_env_var_map(tmp_path: Path):
    _seed_arch_and_manifest(tmp_path)
    rec = _Recorder()
    reg = ProvisioningRegistry(postgres_sql_executor=rec)
    out = await provision_for_agent(
        tmp_path, agent_id="dev", ticket_id="t-001", registry=reg,
    )
    assert "JIG_DEV_MAIN_DB_URL" in out
    assert "search_path%3Dagent_t_001" in out["JIG_DEV_MAIN_DB_URL"]
    assert rec.calls == ["CREATE SCHEMA IF NOT EXISTS agent_t_001"]


@pytest.mark.asyncio
async def test_provision_for_agent_swallows_errors(tmp_path: Path):
    """Provisioner exceptions short-circuit to an empty env map."""
    _seed_arch_and_manifest(tmp_path)

    class _Boom(PostgresSchemaProvisioner):
        async def provision(self, service, namespace):  # type: ignore[override]
            raise RuntimeError("nope")

    reg = ProvisioningRegistry()
    reg.register("postgres", _Boom())
    out = await provision_for_agent(
        tmp_path, agent_id="d", ticket_id="t-1", registry=reg,
    )
    assert out == {}


@pytest.mark.asyncio
async def test_cleanup_for_agent_no_manifest_no_op(tmp_path: Path):
    """No manifest → nothing to clean; must not raise."""
    await cleanup_for_agent(
        tmp_path, agent_id="a", ticket_id="t", success=True
    )


@pytest.mark.asyncio
async def test_cleanup_for_agent_drops_or_archives(tmp_path: Path):
    _seed_arch_and_manifest(tmp_path)
    rec = _Recorder()
    reg = ProvisioningRegistry(postgres_sql_executor=rec)

    await cleanup_for_agent(
        tmp_path, agent_id="d", ticket_id="t-001", success=True, registry=reg,
    )
    assert rec.calls == ["DROP SCHEMA IF EXISTS agent_t_001 CASCADE"]
    rec.calls.clear()
    await cleanup_for_agent(
        tmp_path, agent_id="d", ticket_id="t-001", success=False, registry=reg,
    )
    assert rec.calls == [
        "ALTER SCHEMA agent_t_001 RENAME TO archived_agent_t_001"
    ]


# ---------------------------------------------------------------------------
# Orchestrator integration — env-var injection through _run_agent_with_analytics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_agent_with_analytics_injects_dev_env_vars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When a manifest exists, ``ctx.extra_env`` carries the URL map."""
    _save_project(tmp_path)
    _seed_arch_and_manifest(tmp_path)
    orch = Orchestrator(project_path=tmp_path)
    await orch.startup()
    try:
        from jig import orchestrator as orchestrator_module

        captured: dict = {}

        class _FakeResult:
            status = "success"
            final_text = "ok"
            total_cost_usd = None
            tokens_in = None
            tokens_out = None

        async def _fake_run_agent(ctx, emitter=None):
            captured["extra_env"] = dict(getattr(ctx, "extra_env", None) or {})
            return _FakeResult()

        monkeypatch.setattr(orchestrator_module, "run_agent", _fake_run_agent)

        # Replace the orchestrator's hook helpers with versions that
        # use an in-memory recorder so no live Postgres is needed.
        rec = _Recorder()
        reg = ProvisioningRegistry(postgres_sql_executor=rec)

        async def _provision(project_path, *, agent_id, ticket_id, epic_id=None, registry=None):
            return await provision_for_agent(
                project_path,
                agent_id=agent_id,
                ticket_id=ticket_id,
                epic_id=epic_id,
                registry=reg,
            )

        async def _cleanup(project_path, *, agent_id, ticket_id, success, epic_id=None, registry=None):
            await cleanup_for_agent(
                project_path,
                agent_id=agent_id,
                ticket_id=ticket_id,
                success=success,
                epic_id=epic_id,
                registry=reg,
            )

        monkeypatch.setattr(
            orchestrator_module, "provision_for_agent", _provision
        )
        monkeypatch.setattr(
            orchestrator_module, "cleanup_for_agent", _cleanup
        )

        ticket = Ticket(
            id="t-001", work_type=WorkType.FEATURE, title="t", created_by="u"
        )

        class _FakeCtx:
            role = "dev"
            extra_env: dict[str, str] | None = None

            def __init__(self, t: Ticket) -> None:
                self.ticket = t

        await orch._run_agent_with_analytics(_FakeCtx(ticket))

        # Provisioning fired and the env-var map was passed to run_agent.
        assert "JIG_DEV_MAIN_DB_URL" in captured["extra_env"]
        # Cleanup fired with success=True (drop).
        assert any("CREATE SCHEMA" in c for c in rec.calls)
        assert any("DROP SCHEMA" in c for c in rec.calls)
    finally:
        await orch.shutdown()


@pytest.mark.asyncio
async def test_run_agent_with_analytics_archives_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failure path triggers archive (the operator-inspect default)."""
    _save_project(tmp_path)
    _seed_arch_and_manifest(tmp_path)
    orch = Orchestrator(project_path=tmp_path)
    await orch.startup()
    try:
        from jig import orchestrator as orchestrator_module

        async def _boom(ctx, emitter=None):
            raise RuntimeError("crash")

        monkeypatch.setattr(orchestrator_module, "run_agent", _boom)

        rec = _Recorder()
        reg = ProvisioningRegistry(postgres_sql_executor=rec)

        async def _provision(project_path, *, agent_id, ticket_id, epic_id=None, registry=None):
            return await provision_for_agent(
                project_path,
                agent_id=agent_id,
                ticket_id=ticket_id,
                epic_id=epic_id,
                registry=reg,
            )

        async def _cleanup(project_path, *, agent_id, ticket_id, success, epic_id=None, registry=None):
            await cleanup_for_agent(
                project_path,
                agent_id=agent_id,
                ticket_id=ticket_id,
                success=success,
                epic_id=epic_id,
                registry=reg,
            )

        monkeypatch.setattr(orchestrator_module, "provision_for_agent", _provision)
        monkeypatch.setattr(orchestrator_module, "cleanup_for_agent", _cleanup)

        ticket = Ticket(
            id="t-001", work_type=WorkType.FEATURE, title="t", created_by="u"
        )

        class _FakeCtx:
            role = "dev"
            extra_env: dict[str, str] | None = None

            def __init__(self, t: Ticket) -> None:
                self.ticket = t

        with pytest.raises(RuntimeError):
            await orch._run_agent_with_analytics(_FakeCtx(ticket))
        # Archive ran (failure path)
        assert any("ALTER SCHEMA" in c for c in rec.calls)
    finally:
        await orch.shutdown()


@pytest.mark.asyncio
async def test_orchestrator_skips_dev_env_when_no_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No manifest means no env-var map; provisioning is a clean no-op."""
    _save_project(tmp_path)  # no manifest seeded
    orch = Orchestrator(project_path=tmp_path)
    await orch.startup()
    try:
        from jig import orchestrator as orchestrator_module

        captured: dict = {}

        class _FakeResult:
            status = "success"
            final_text = "ok"
            total_cost_usd = None
            tokens_in = None
            tokens_out = None

        async def _fake_run_agent(ctx, emitter=None):
            await asyncio.sleep(0)
            captured["extra_env"] = dict(getattr(ctx, "extra_env", None) or {})
            return _FakeResult()

        monkeypatch.setattr(orchestrator_module, "run_agent", _fake_run_agent)

        ticket = Ticket(
            id="t-002", work_type=WorkType.FEATURE, title="t", created_by="u"
        )

        class _FakeCtx:
            role = "dev"
            extra_env: dict[str, str] | None = None

            def __init__(self, t: Ticket) -> None:
                self.ticket = t

        await orch._run_agent_with_analytics(_FakeCtx(ticket))
        assert captured["extra_env"] == {}
    finally:
        await orch.shutdown()
