"""Verify role-scoped registration of L3 PO MCP tools (Track B5)."""
from __future__ import annotations

from pathlib import Path

import pytest

import jig.mcp_server as mcp_server_mod
from jig.mcp_server import create_agent_mcp_server
from jig.models import RoleConfig
from jig.persistence import load_role
from jig.store.bus import MessageBus
from jig.store.memory import MemoryStore
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore


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
async def test_l3_po_server_exposes_l3_finalize(tmp_path, stores, monkeypatch):
    tickets, threads, memory, bus = stores
    cfg = RoleConfig(
        role="po-l3",
        allowed_tools=["Read", "ask_question", "l3_finalize"],
        strict_tools=True,
    )
    captured: dict = {}
    _spy_factory(monkeypatch, captured)
    create_agent_mcp_server(
        tickets=tickets,
        threads=threads,
        memory=memory,
        bus=bus,
        agent_role="po-l3",
        agent_cfg=cfg,
        worktree_path=tmp_path,
        project_path=tmp_path,
    )
    names = _names(captured)
    assert "l3_finalize" in names
    assert "ask_question" in names


@pytest.mark.asyncio
async def test_l3_finalize_not_registered_when_not_allowed(
    tmp_path, stores, monkeypatch
):
    """Strict-tools gating must keep l3_finalize off other roles' servers."""
    tickets, threads, memory, bus = stores
    cfg = RoleConfig(
        role="po",  # v1 PO; gets brief_* tools, not l3_finalize.
        allowed_tools=["brief_set_section", "po_finish_brief"],
        strict_tools=True,
    )
    captured: dict = {}
    _spy_factory(monkeypatch, captured)
    create_agent_mcp_server(
        tickets=tickets,
        threads=threads,
        memory=memory,
        bus=bus,
        agent_role="po",
        agent_cfg=cfg,
        worktree_path=tmp_path,
        project_path=tmp_path,
    )
    assert "l3_finalize" not in _names(captured)


def test_l3_po_role_config_loads(tmp_path):
    """The shipped l3_po.yaml is well-formed and grants the L3 toolset."""
    cfg = load_role(tmp_path, "l3_po")
    assert cfg.role == "po-l3"
    assert cfg.strict_tools is True
    assert "l3_finalize" in cfg.allowed_tools
    assert "ask_question" in cfg.allowed_tools
    # L3 needs Read so the agent can pull project.md / discovery.md /
    # suites.yaml from the worktree without going through MCP.
    assert "Read" in cfg.allowed_tools
    # L3 must not see other phases' authoring tools — strict scoping
    # keeps it from wandering into v1 brief_* (different format),
    # l0_finalize (already done), or spec_publish (spec-generator's job).
    for forbidden in (
        "brief_set_section",
        "po_finish_brief",
        "l0_finalize",
        "spec_publish",
        "spec_report_gaps",
        "arch_set_field",
    ):
        assert forbidden not in cfg.allowed_tools
