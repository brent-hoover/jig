"""Verify role-scoped registration of the SA MVP incremental tools (Track C MVP).

Twelve new tools (11 upserts + ``arch_finalize``) get gated on
``allowed_tools`` exactly like the bones one-shot ``sa_finalize``.
Tests pin two things:

1. A role with all 12 in ``allowed_tools`` sees all 12 on its server.
2. Strict-tools gating keeps them off other roles' servers (e.g. the
   bones ``sa-v2`` role still sees only its one-shot tool, not the
   new incremental surface).
"""
from __future__ import annotations

from pathlib import Path

import pytest

import jig.mcp_server as mcp_server_mod
from jig.mcp_server import create_agent_mcp_server
from jig.models import RoleConfig
from jig.store.bus import MessageBus
from jig.store.memory import MemoryStore
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore


_INCREMENTAL_TOOLS = {
    "arch_set_module",
    "arch_set_data_store",
    "arch_set_shared_contract",
    "arch_set_cross_cutting_policy",
    "arch_set_open_question",
    "module_set_owned_collection",
    "module_set_external_dependency",
    "module_set_integration_ac",
    "module_set_behavioral_contract",
    "module_set_data_contract",
    "module_set_open_question",
    "arch_finalize",
}


@pytest.fixture
async def stores(tmp_path: Path):
    tickets = TicketStore(tmp_path / "tickets.jsonl")
    threads = ThreadStore(tmp_path / "comments.jsonl")
    memory = MemoryStore(tmp_path)
    bus = MessageBus(tmp_path / "messages.jsonl")
    for s in (tickets, threads, memory, bus):
        await s.load()
    return tickets, threads, memory, bus


def _spy_factory(monkeypatch, captured):
    real = mcp_server_mod.create_sdk_mcp_server

    def spy(*, name, tools):
        captured["tools"] = tools
        return real(name=name, tools=tools)

    monkeypatch.setattr(mcp_server_mod, "create_sdk_mcp_server", spy)


def _names(captured):
    return {t.name for t in captured["tools"]}


@pytest.mark.asyncio
async def test_sa_mvp_role_exposes_all_incremental_tools(
    tmp_path, stores, monkeypatch
):
    tickets, threads, memory, bus = stores
    cfg = RoleConfig(
        role="sa-mvp",
        allowed_tools=["Read", "ask_question", *sorted(_INCREMENTAL_TOOLS)],
        strict_tools=True,
    )
    captured: dict = {}
    _spy_factory(monkeypatch, captured)
    create_agent_mcp_server(
        tickets=tickets,
        threads=threads,
        memory=memory,
        bus=bus,
        agent_role="sa-mvp",
        agent_cfg=cfg,
        worktree_path=tmp_path,
        project_path=tmp_path,
    )
    names = _names(captured)
    missing = _INCREMENTAL_TOOLS - names
    assert not missing, f"missing tools on sa-mvp server: {sorted(missing)!r}"


def test_sa_mvp_role_config_loads(tmp_path):
    """The shipped sa_mvp.yaml is well-formed and grants the MVP toolset."""
    from jig.persistence import load_role

    cfg = load_role(tmp_path, "sa_mvp")
    assert cfg.role == "sa-mvp"
    assert cfg.strict_tools is True
    # Every incremental tool listed in allowed_tools.
    granted = set(cfg.allowed_tools)
    missing = _INCREMENTAL_TOOLS - granted
    assert not missing, f"sa_mvp.yaml missing tools: {sorted(missing)!r}"
    # Read + ask_question — the SA's two non-authoring tools.
    assert "Read" in granted
    assert "ask_question" in granted
    # MVP role must not see the bones one-shot or v1 paths.
    for forbidden in (
        "sa_finalize",
        "arch_set_field",
        "sa_propose_scaffold",
        "l0_finalize",
        "l3_finalize",
        "spec_publish",
    ):
        assert forbidden not in granted


@pytest.mark.asyncio
async def test_incremental_tools_not_registered_when_not_allowed(
    tmp_path, stores, monkeypatch
):
    """Strict-tools gating keeps the MVP surface off roles that don't grant it.

    The bones SA role only grants ``sa_finalize`` — it must not see
    any of the new incremental tools.
    """
    tickets, threads, memory, bus = stores
    cfg = RoleConfig(
        role="sa-v2",
        allowed_tools=["Read", "ask_question", "sa_finalize"],
        strict_tools=True,
    )
    captured: dict = {}
    _spy_factory(monkeypatch, captured)
    create_agent_mcp_server(
        tickets=tickets,
        threads=threads,
        memory=memory,
        bus=bus,
        agent_role="sa-v2",
        agent_cfg=cfg,
        worktree_path=tmp_path,
        project_path=tmp_path,
    )
    names = _names(captured)
    leaked = _INCREMENTAL_TOOLS & names
    assert not leaked, f"incremental tools leaked onto sa-v2: {sorted(leaked)!r}"
    # sa-v2's one-shot path is untouched.
    assert "sa_finalize" in names
