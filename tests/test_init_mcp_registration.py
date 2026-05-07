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
            "arch_list_templates",
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
        "arch_list_templates",
        "sa_propose_scaffold",
        "spec_get_field",
    } <= names
    assert not any(n.startswith("brief_") for n in names)


@pytest.mark.asyncio
async def test_strict_tools_drops_unlisted_base_tools(
    tmp_path, stores, monkeypatch
) -> None:
    """``strict_tools=True`` makes ``allowed_tools`` authoritative for
    base tools too — without it PO can call ``commit_progress`` /
    ``update_ticket`` via ToolSearch and waste turns hunting for a
    git repo or messing with ticket state it shouldn't touch."""
    tickets, threads, memory, bus = stores
    cfg = RoleConfig(
        role="po",
        allowed_tools=[
            "ask_question",
            "brief_get_section",
            "brief_list_sections",
            "brief_set_section",
            "po_finish_brief",
        ],
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
    names = _tool_names(captured)
    # Listed PO tools survive.
    assert {
        "ask_question",
        "brief_get_section",
        "brief_list_sections",
        "brief_set_section",
        "po_finish_brief",
    } == names
    # The legacy base tools that previously leaked into every role are
    # gone.
    for blocked in (
        "commit_progress",
        "update_ticket",
        "create_ticket",
        "record_learning",
        "request_context",
        "thread_handoff",
        "list_tickets",
        "read_comments",
    ):
        assert blocked not in names, (
            f"strict_tools failed to drop {blocked!r}: {sorted(names)}"
        )


def test_strict_disallowed_tools_blocks_dangerous_builtins():
    """Strict-tools roles need their built-in tool surface narrowed —
    PO/SA were observed reaching for Bash/Glob/Agent under
    bypassPermissions because allowed_tools alone doesn't gate them.
    The deny list closes that loop."""
    from jig.agent import _strict_disallowed_tools

    out = _strict_disallowed_tools(["Read", "ask_question"])
    # All the exploratory/mutating builtins are denied for a role that
    # didn't explicitly opt in.
    for name in ("Bash", "Edit", "Write", "Glob", "Grep", "Agent",
                 "WebSearch", "WebFetch", "NotebookEdit"):
        assert name in out, f"{name} should be blocked: {out}"
    # Read is in the deny list by default; this role opted in by
    # listing Read in allowed_tools, so it's removed from the deny list.
    assert "Read" not in out


def test_strict_disallowed_tools_blocks_read_when_not_opted_in():
    """Read is denied by default for strict roles — the PO's brief
    lives in MCP, and letting Read through caused agents to chase
    non-existent brief.md files."""
    from jig.agent import _strict_disallowed_tools

    out = _strict_disallowed_tools(["ask_question", "brief_get_section"])
    assert "Read" in out, f"Read should be blocked when not opted in: {out}"


def test_strict_disallowed_tools_respects_explicit_optin():
    """If a strict role names a normally-blocked tool in allowed_tools,
    drop it from the deny list — operators may want a strict role
    that can still run a specific Bash tool, etc."""
    from jig.agent import _strict_disallowed_tools

    out = _strict_disallowed_tools(["Read", "Bash"])
    assert "Bash" not in out
    # Other dangerous builtins remain blocked.
    assert "Glob" in out
    assert "Edit" in out


@pytest.mark.asyncio
async def test_strict_tools_off_keeps_legacy_base_tools(
    tmp_path, stores, monkeypatch
) -> None:
    """Operational roles (dev, test, pm) leave ``strict_tools=False``
    and continue to receive the unconditional base toolset."""
    tickets, threads, memory, bus = stores
    cfg = RoleConfig(role="dev", allowed_tools=[])
    assert cfg.strict_tools is False
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
    names = _tool_names(captured)
    assert {"commit_progress", "thread_handoff", "create_ticket"} <= names


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


@pytest.mark.asyncio
async def test_sa_server_registers_capability_aware_tools(
    tmp_path, stores, monkeypatch,
):
    tickets, threads, memory, bus = stores
    cfg = RoleConfig(
        role="sa",
        allowed_tools=[
            "spec_list_capabilities",
            "spec_get_capability",
            "spec_get_behavior",
            "spec_list_non_goals",
            "spec_get_non_goal",
            "spec_resolve_uri",
            "spec_load_existing",
        ],
        strict_tools=True,
    )
    captured: dict = {}
    _spy_factory(monkeypatch, captured)
    create_agent_mcp_server(
        tickets=tickets, threads=threads, memory=memory, bus=bus,
        agent_role="sa", agent_cfg=cfg,
        worktree_path=tmp_path, project_path=tmp_path,
    )
    names = _tool_names(captured)
    assert {
        "spec_list_capabilities",
        "spec_get_capability",
        "spec_get_behavior",
        "spec_list_non_goals",
        "spec_get_non_goal",
        "spec_resolve_uri",
        "spec_load_existing",
    } == names
