"""Verify role-scoped registration of L0 PO MCP tools (Track B1)."""

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
async def test_l0_po_server_exposes_l0_finalize(tmp_path, stores, monkeypatch):
    tickets, threads, memory, bus = stores
    cfg = RoleConfig(
        role="po-l0",
        allowed_tools=["ask_question", "l0_finalize"],
        strict_tools=True,
    )
    captured: dict = {}
    _spy_factory(monkeypatch, captured)
    create_agent_mcp_server(
        tickets=tickets,
        threads=threads,
        memory=memory,
        bus=bus,
        agent_role="po-l0",
        agent_cfg=cfg,
        worktree_path=tmp_path,
        project_path=tmp_path,
    )
    names = _names(captured)
    assert names == {"ask_question", "l0_finalize"}


@pytest.mark.asyncio
async def test_l0_finalize_not_registered_when_not_allowed(
    tmp_path, stores, monkeypatch
):
    tickets, threads, memory, bus = stores
    cfg = RoleConfig(
        role="po",
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
    assert "l0_finalize" not in _names(captured)


def test_l0_po_role_config_loads(tmp_path):
    """The shipped l0_po.yaml is well-formed and grants the L0 toolset."""
    cfg = load_role(tmp_path, "l0_po")
    assert cfg.role == "po-l0"
    assert cfg.strict_tools is True
    assert "l0_finalize" in cfg.allowed_tools
    assert "ask_question" in cfg.allowed_tools
    # Out-of-scope tools must not leak in — the L0 PO writes one set of
    # artifacts and exits; it has no business with brief_* (v1 path) or
    # spec_* (spec-generator) tools.
    for forbidden in (
        "brief_set_section",
        "po_finish_brief",
        "spec_publish",
        "spec_report_gaps",
        "arch_set_field",
    ):
        assert forbidden not in cfg.allowed_tools
