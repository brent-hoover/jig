"""Verify role-scoped registration of L2 PO MCP tools (Track B4 MVP)."""
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


# The L2 PO's MCP toolset per design.md §"L2 PO tools" — for MVP scope
# this is one finalize tool plus the conversation primitives. The full
# multi-turn proposing UX surfaces via the role prompt; the MCP surface
# is one tool.
EXPECTED_L2_TOOLS = {
    "Read",
    "ask_question",
    "l2_finalize",
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


def test_l2_po_role_config_loads(tmp_path: Path):
    """The shipped l2_po.yaml is well-formed and grants the L2 toolset."""
    cfg = load_role(tmp_path, "l2_po")
    assert cfg.role == "po-l2"
    assert cfg.strict_tools is True
    assert set(cfg.allowed_tools) == EXPECTED_L2_TOOLS


def test_l2_po_role_excludes_other_phase_tools(tmp_path: Path):
    """Strict scoping: L2 must not see L0 / L1 / L3 / SA tooling."""
    cfg = load_role(tmp_path, "l2_po")
    for forbidden in (
        "l0_finalize",
        "discovery_finalize",
        "discovery_add_journey",
        "l3_finalize",
        "sa_finalize",
        "plan_finalize",
        "brief_set_section",
        "po_finish_brief",
        "spec_publish",
        "arch_set_field",
    ):
        assert forbidden not in cfg.allowed_tools


def test_l2_po_role_phase_prompt_is_substantive(tmp_path: Path):
    """The phase prompt must carry the load-bearing L2 guidance.

    Spot-checks a few key terms so a future edit that drops the
    grouping discipline fails this test rather than silently shipping
    a PO that won't follow the constraints.
    """
    cfg = load_role(tmp_path, "l2_po")
    prompt = cfg.phase_prompt
    for needle in (
        "Suite Organizer",
        "exactly one suite",
        "3-5 capabilities",
        "operator's vocabulary",
        "l2_finalize",
    ):
        assert needle in prompt, f"missing prompt fragment: {needle!r}"


@pytest.mark.asyncio
async def test_l2_po_server_exposes_l2_finalize(tmp_path, stores, monkeypatch):
    tickets, threads, memory, bus = stores
    cfg = RoleConfig(
        role="po-l2",
        allowed_tools=sorted(EXPECTED_L2_TOOLS),
        strict_tools=True,
    )
    captured: dict = {}
    _spy_factory(monkeypatch, captured)
    create_agent_mcp_server(
        tickets=tickets,
        threads=threads,
        memory=memory,
        bus=bus,
        agent_role="po-l2",
        agent_cfg=cfg,
        worktree_path=tmp_path,
        project_path=tmp_path,
    )
    names = _names(captured)
    # Read is the SDK's built-in filesystem read, gated at use-time.
    expected = EXPECTED_L2_TOOLS - {"Read"}
    assert names == expected


@pytest.mark.asyncio
async def test_l2_finalize_not_registered_when_not_allowed(
    tmp_path, stores, monkeypatch
):
    """Strict-tools gating must keep l2_finalize off other roles' servers."""
    tickets, threads, memory, bus = stores
    cfg = RoleConfig(
        role="po-l3",  # downstream phase; gets l3_finalize, not l2.
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
    assert "l2_finalize" not in _names(captured)
