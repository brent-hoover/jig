"""Verify role-scoped registration of Planner PM MCP tools (Track F MVP)."""
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
async def test_planner_pm_server_exposes_plan_finalize(
    tmp_path, stores, monkeypatch
):
    tickets, threads, memory, bus = stores
    cfg = RoleConfig(
        role="planner-pm",
        allowed_tools=["Read", "ask_question", "plan_finalize"],
        strict_tools=True,
    )
    captured: dict = {}
    _spy_factory(monkeypatch, captured)
    create_agent_mcp_server(
        tickets=tickets,
        threads=threads,
        memory=memory,
        bus=bus,
        agent_role="planner-pm",
        agent_cfg=cfg,
        worktree_path=tmp_path,
        project_path=tmp_path,
    )
    names = _names(captured)
    assert "plan_finalize" in names
    assert "ask_question" in names


@pytest.mark.asyncio
async def test_plan_finalize_not_registered_when_not_allowed(
    tmp_path, stores, monkeypatch
):
    """Strict-tools gating must keep plan_finalize off other roles' servers."""
    tickets, threads, memory, bus = stores
    cfg = RoleConfig(
        role="sa-v2",  # SA-v2 doesn't author the build plan.
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
    assert "plan_finalize" not in _names(captured)


def test_planner_pm_role_config_loads(tmp_path):
    """The shipped planner_pm.yaml is well-formed and grants the MVP toolset."""
    cfg = load_role(tmp_path, "planner_pm")
    assert cfg.role == "planner-pm"
    assert cfg.strict_tools is True
    assert "plan_finalize" in cfg.allowed_tools
    assert "ask_question" in cfg.allowed_tools
    # Planner needs Read so the agent can pull brief.md, suites.yaml,
    # spec.structured.yaml, architecture.yaml, and contracts.yaml from
    # the worktree without going through MCP.
    assert "Read" in cfg.allowed_tools
    # Planner must not see other phases' authoring tools — keeps it out
    # of v1 arch_set_field / v1 sa_propose_scaffold and out of any L0 /
    # L3 / SA finalize tools.
    for forbidden in (
        "arch_set_field",
        "sa_propose_scaffold",
        "sa_finalize",
        "l0_finalize",
        "l3_finalize",
        "spec_publish",
        "brief_set_section",
    ):
        assert forbidden not in cfg.allowed_tools
