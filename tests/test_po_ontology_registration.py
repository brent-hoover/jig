"""Verify role-scoped registration of project ontology MCP tools (Track B6 MVP)."""

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


# The four ontology tools per design.md §"Project ontology". The L1 PO
# is the primary author (terms get captured during journey walks);
# downstream agents lift them via ``ontology_get_terms`` /
# ``ontology_lookup``.
ONTOLOGY_TOOLS = {
    "ontology_stash_term",
    "ontology_add_term",
    "ontology_get_terms",
    "ontology_lookup",
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


def test_l1_po_role_grants_ontology_tools(tmp_path: Path):
    """The shipped l1_po.yaml grants all four ontology tools — the L1
    PO is the primary author per design.md."""
    cfg = load_role(tmp_path, "l1_po")
    for needed in ONTOLOGY_TOOLS:
        assert needed in cfg.allowed_tools


def test_l1_po_prompt_documents_ontology_capture(tmp_path: Path):
    """The L1 prompt must instruct PO to capture domain terms during
    journey walks — design.md tenet, not just implementation detail."""
    cfg = load_role(tmp_path, "l1_po")
    prompt = cfg.phase_prompt
    assert "ontology_stash_term" in prompt
    assert "ontology_add_term" in prompt
    # The narrative reason — captures the why, not just the call.
    assert "domain" in prompt.lower()


@pytest.mark.asyncio
async def test_ontology_tools_register_under_l1_po(tmp_path, stores, monkeypatch):
    """A role with all four ontology tools sees them on its MCP server."""
    tickets, threads, memory, bus = stores
    cfg = RoleConfig(
        role="po-l1",
        allowed_tools=sorted(ONTOLOGY_TOOLS | {"ask_question"}),
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
    assert ONTOLOGY_TOOLS.issubset(names)


@pytest.mark.asyncio
async def test_ontology_tools_not_registered_when_not_allowed(
    tmp_path, stores, monkeypatch
):
    """Strict-tools gating must keep ontology tools off other roles' servers."""
    tickets, threads, memory, bus = stores
    cfg = RoleConfig(
        role="po-l3",  # downstream phase; ontology authoring is L1's job.
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
    for forbidden in ONTOLOGY_TOOLS:
        assert forbidden not in _names(captured)


# ---- operator-edit affordances (Track B Final) ---------------------------


OPERATOR_EDIT_TOOLS = {
    "ontology_edit_term",
    "ontology_remove_term",
    "ontology_find_references",
}


@pytest.mark.asyncio
async def test_operator_edit_tools_register_when_allowed(tmp_path, stores, monkeypatch):
    """Track B Final adds three operator-edit tools that the L1 PO can
    invoke when the operator asks for a revision mid-walk."""
    tickets, threads, memory, bus = stores
    cfg = RoleConfig(
        role="po-l1",
        allowed_tools=sorted(OPERATOR_EDIT_TOOLS | {"ask_question"}),
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
    assert OPERATOR_EDIT_TOOLS.issubset(_names(captured))


@pytest.mark.asyncio
async def test_operator_edit_tools_not_registered_when_not_allowed(
    tmp_path, stores, monkeypatch
):
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
    for forbidden in OPERATOR_EDIT_TOOLS:
        assert forbidden not in _names(captured)
