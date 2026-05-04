"""Verify role-scoped registration of the VD MCP tools (Track D MVP).

Eight new tools — six VD-specific upserts/finalize plus the linter +
notes wrappers. Tests pin two things:

1. A role with all of them in ``allowed_tools`` sees them on its server.
2. Strict-tools gating keeps them off roles that don't grant them.
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


_VD_TOOLS = {
    "vd_finalize",
    "vd_set_wireframe",
    "vd_set_design_token",
    "vd_set_component",
    "wireframe_lint",
    "wireframe_get_notes",
    "wireframe_set_notes",
    "vd_import_claude_design",
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
async def test_vd_role_exposes_all_vd_tools(
    tmp_path, stores, monkeypatch
):
    tickets, threads, memory, bus = stores
    cfg = RoleConfig(
        role="vd",
        allowed_tools=["Read", "ask_question", *sorted(_VD_TOOLS)],
        strict_tools=True,
    )
    captured: dict = {}
    _spy_factory(monkeypatch, captured)
    create_agent_mcp_server(
        tickets=tickets,
        threads=threads,
        memory=memory,
        bus=bus,
        agent_role="vd",
        agent_cfg=cfg,
        worktree_path=tmp_path,
        project_path=tmp_path,
    )
    names = _names(captured)
    missing = _VD_TOOLS - names
    assert not missing, f"missing tools on vd server: {sorted(missing)!r}"


@pytest.mark.asyncio
async def test_vd_tools_not_registered_when_not_allowed(
    tmp_path, stores, monkeypatch
):
    """Strict-tools gating keeps VD tools off roles that don't grant them."""
    tickets, threads, memory, bus = stores
    cfg = RoleConfig(
        role="dev",
        allowed_tools=["Read", "Write", "Edit"],
        strict_tools=True,
    )
    captured: dict = {}
    _spy_factory(monkeypatch, captured)
    create_agent_mcp_server(
        tickets=tickets,
        threads=threads,
        memory=memory,
        bus=bus,
        agent_role="dev",
        agent_cfg=cfg,
        worktree_path=tmp_path,
        project_path=tmp_path,
    )
    names = _names(captured)
    leaked = _VD_TOOLS & names
    assert not leaked, f"VD tools leaked onto dev: {sorted(leaked)!r}"
