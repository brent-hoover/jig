"""Verify role-scoped registration of L1 PO MCP tools (Track B MVP)."""
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


# The L1 PO's MCP toolset per design.md §"L1 PO tools (new MCP tools)".
# Kept in this test so the role config and the MCP server registration
# stay aligned via a single source of truth — adding a tool to the
# role config without registering it here will fail loudly.
EXPECTED_L1_TOOLS = {
    "Read",
    "ask_question",
    "discovery_set_intro",
    "discovery_add_persona",
    "discovery_set_phase",
    "discovery_set_next_question",
    "discovery_add_journey",
    "discovery_add_capability",
    "discovery_stash_pending_capability",
    "discovery_clear_pending",
    "discovery_set_playback",
    "discovery_load_state",
    "discovery_finalize",
}


def test_l1_po_role_config_loads(tmp_path: Path):
    """The shipped l1_po.yaml is well-formed and grants the L1 toolset."""
    cfg = load_role(tmp_path, "l1_po")
    assert cfg.role == "po-l1"
    assert cfg.strict_tools is True
    # Every MCP tool the design enumerates is granted; no extras.
    assert set(cfg.allowed_tools) == EXPECTED_L1_TOOLS


def test_l1_po_role_excludes_other_phase_tools(tmp_path: Path):
    """Strict scoping: L1 must not see L0 / L3 / SA / v1-brief tooling."""
    cfg = load_role(tmp_path, "l1_po")
    for forbidden in (
        "l0_finalize",       # L0 already done by upstream phase
        "l3_finalize",       # downstream phase
        "sa_finalize",       # SA's job
        "plan_finalize",     # PM's job
        "brief_set_section", # v1 monolithic-brief tooling
        "po_finish_brief",   # v1 PO finish
        "spec_publish",      # spec-generator
        "spec_report_gaps",
        "arch_set_field",    # v1 SA tooling
    ):
        assert forbidden not in cfg.allowed_tools


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
async def test_l1_po_server_exposes_full_toolset(tmp_path, stores, monkeypatch):
    """Strict-mode L1 PO server exposes exactly the L1 toolset."""
    tickets, threads, memory, bus = stores
    cfg = RoleConfig(
        role="po-l1",
        allowed_tools=sorted(EXPECTED_L1_TOOLS),
        strict_tools=True,
    )
    captured: dict = {}
    _spy_factory(monkeypatch, captured)
    create_agent_mcp_server(
        tickets=tickets,
        threads=threads,
        memory=memory,
        bus=bus,
        agent_role="po-l1",
        agent_cfg=cfg,
        worktree_path=tmp_path,
        project_path=tmp_path,
    )
    names = _names(captured)
    # MCP server resolves Read via the SDK so it shows in the tool list
    # only when registered as a Jig tool — Read is the SDK's built-in
    # filesystem read, gated by the role's allowed_tools at use-time.
    # The MCP server's name set should match the L1 tool set minus Read.
    expected = EXPECTED_L1_TOOLS - {"Read"}
    assert names == expected


@pytest.mark.asyncio
async def test_l1_finalize_not_registered_when_not_allowed(
    tmp_path, stores, monkeypatch
):
    """Strict-tools gating must keep discovery_finalize off other roles."""
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
    for forbidden in EXPECTED_L1_TOOLS - {"Read", "ask_question"}:
        assert forbidden not in names


def test_l1_po_role_phase_prompt_is_substantive(tmp_path: Path):
    """The phase prompt must carry the load-bearing 5-phase guidance.

    Spot-checks key terms so a future edit that accidentally drops the
    phase walkthrough fails this test rather than silently shipping a
    PO that won't follow the discipline.
    """
    cfg = load_role(tmp_path, "l1_po")
    prompt = cfg.phase_prompt
    # The 5 phases by name.
    for needle in (
        "Phase 1",
        "Phase 2",
        "Phase 3",
        "Phase 4",
        "Phase 5",
        "primary persona",
        "primary job",
        "Walk the journey",
        "failure modes",
        "play back",
    ):
        assert needle in prompt, f"missing prompt fragment: {needle!r}"
    # The "one question per turn" mechanic — non-negotiable per design.
    assert "One question per turn" in prompt
