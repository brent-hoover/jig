"""Verify role-scoped registration of init-workflow MCP tools.

These tests exercise ``create_agent_mcp_server`` to confirm that the
brief / spec / architecture tools defined in ``jig.init_mcp`` are
registered if and only if the role's ``allowed_tools`` list names them.

Discovery: ``create_sdk_mcp_server`` returns a plain ``dict`` of shape
``{"type": "sdk", "name": ..., "instance": <mcp Server>}`` and the
underlying Server object does not expose a ``tools`` attribute. We
follow the same pattern used in ``tests/test_capabilities.py`` and spy
on ``create_sdk_mcp_server`` to capture the list of tools the factory
hands to the SDK; tool names are then read off ``t.name``.
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


@pytest.fixture
async def stores(tmp_path: Path):
    tickets = TicketStore(tmp_path / "tickets.jsonl")
    threads = ThreadStore(tmp_path / "comments.jsonl")
    memory = MemoryStore(tmp_path)
    bus = MessageBus(tmp_path / "messages.jsonl")
    for s in (tickets, threads, memory, bus):
        await s.load()
    return tickets, threads, memory, bus


def _spy_factory(monkeypatch: pytest.MonkeyPatch, captured: dict) -> None:
    real = mcp_server_mod.create_sdk_mcp_server

    def spy(*, name: str, tools: list) -> object:
        captured["tools"] = tools
        return real(name=name, tools=tools)

    monkeypatch.setattr(mcp_server_mod, "create_sdk_mcp_server", spy)


def _tool_names(captured: dict) -> set[str]:
    return {t.name for t in captured["tools"]}


@pytest.mark.asyncio
async def test_po_server_exposes_brief_tools(
    tmp_path: Path, stores, monkeypatch: pytest.MonkeyPatch
) -> None:
    tickets, threads, memory, bus = stores
    cfg = RoleConfig(
        role="po",
        allowed_tools=[
            "brief_get_section",
            "brief_list_sections",
            "brief_set_section",
            "po_finish_brief",
        ],
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
    names = _tool_names(captured)
    assert {
        "brief_get_section",
        "brief_list_sections",
        "brief_set_section",
        "po_finish_brief",
    } <= names


@pytest.mark.asyncio
async def test_sa_server_exposes_arch_tools_not_brief(
    tmp_path: Path, stores, monkeypatch: pytest.MonkeyPatch
) -> None:
    tickets, threads, memory, bus = stores
    cfg = RoleConfig(
        role="sa",
        allowed_tools=[
            "spec_get_field",
            "spec_list_fields",
            "arch_get_field",
            "arch_set_field",
            "arch_list_fields",
            "sa_propose_scaffold",
        ],
    )
    captured: dict = {}
    _spy_factory(monkeypatch, captured)
    create_agent_mcp_server(
        tickets=tickets,
        threads=threads,
        memory=memory,
        bus=bus,
        agent_role="sa",
        agent_cfg=cfg,
        worktree_path=tmp_path,
        project_path=tmp_path,
    )
    names = _tool_names(captured)
    assert {
        "arch_set_field",
        "arch_get_field",
        "sa_propose_scaffold",
        "spec_get_field",
    } <= names
    assert not any(n.startswith("brief_") for n in names)


@pytest.mark.asyncio
async def test_spec_generator_server_exposes_only_spec_tools(
    tmp_path: Path, stores, monkeypatch: pytest.MonkeyPatch
) -> None:
    tickets, threads, memory, bus = stores
    cfg = RoleConfig(
        role="spec-generator",
        allowed_tools=["spec_publish", "spec_report_gaps"],
    )
    captured: dict = {}
    _spy_factory(monkeypatch, captured)
    create_agent_mcp_server(
        tickets=tickets,
        threads=threads,
        memory=memory,
        bus=bus,
        agent_role="spec-generator",
        agent_cfg=cfg,
        worktree_path=tmp_path,
        project_path=tmp_path,
    )
    names = _tool_names(captured)
    assert {"spec_publish", "spec_report_gaps"} <= names
    assert not any(n.startswith("brief_") for n in names)
    assert not any(n.startswith("arch_") for n in names)
