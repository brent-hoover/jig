"""Regression tests for the sandbox-vs-host cwd mismatch that made
spec-generator (and any agent whose worktree is the project root) read
``/project/...`` paths and miss files visible at ``/workspace/...``.

The fix lives in :func:`jig.sandbox.sandbox_visible_worktree`, which both
the prompt builder's "Working Directory" section and the SDK
``ClaudeAgentOptions.cwd`` route through. Without this, the prompt told
the agent ``cwd=/project`` while bwrap had ``--chdir /workspace``, so
absolute-path tool calls failed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jig.agent import build_agent_prompt
from jig.models import RoleConfig
from jig.project import Project
from jig.runtime import AgentSpawnContext, SpawnReason
from jig.store import MessageBus
from jig.store.memory import MemoryStore
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.ticket import Ticket, WorkType
from tests._test_ticket import TICKET_AC_PLACEHOLDER


async def _make_ctx(tmp_path: Path, worktree_path: Path) -> AgentSpawnContext:
    tickets = TicketStore(tmp_path / "tickets.jsonl")
    await tickets.load()
    threads = ThreadStore(tmp_path / "comments.jsonl")
    await threads.load()
    memory = MemoryStore(tmp_path)
    await memory.load()
    bus = MessageBus(tmp_path / "messages.jsonl")
    await bus.load()
    t = Ticket(
        work_type=WorkType.REFACTOR,
        title="t",
        created_by="o",
        description="do it\n" + TICKET_AC_PLACEHOLDER,
    )
    tid = await tickets.create(t)
    loaded = await tickets.get(tid)
    assert loaded is not None
    return AgentSpawnContext(
        role="dev",
        role_cfg=RoleConfig(role="dev", phase_prompt="be dev"),
        spawn_reason=SpawnReason.PHASE_PRIMARY,
        ticket=loaded,
        parent=None,
        worktree_path=worktree_path,
        project=Project(
            id="p",
            name="p",
            path=str(tmp_path),
            language="python",
            package_manager="uv",
        ),
        tickets=tickets,
        threads=threads,
        memory=memory,
        bus=bus,
    )


async def test_prompt_uses_workspace_when_in_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In-container prompts must report cwd as /workspace, not the host
    worktree path. In production the host path is /project for
    spec-generator, and /project is tmpfs-hidden inside the sandbox —
    so an agent that trusted the host path would resolve absolute reads
    against an empty tmpfs.

    Uses a synthetic absolute host path rather than the literal
    ``/project`` so the test doesn't depend on host-filesystem state
    (jig itself runs in a container that DOES mount /project)."""
    monkeypatch.setenv("JIG_IN_CONTAINER", "1")
    host_worktree = Path("/__jig_test_host_worktree__")
    ctx = await _make_ctx(tmp_path, host_worktree)
    prompt = await build_agent_prompt(ctx)
    assert "working directory is `/workspace`" in prompt
    assert f"working directory is `{host_worktree}`" not in prompt


async def test_prompt_uses_host_path_outside_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("JIG_IN_CONTAINER", raising=False)
    host_worktree = tmp_path / "wt"
    host_worktree.mkdir()
    ctx = await _make_ctx(tmp_path, host_worktree)
    prompt = await build_agent_prompt(ctx)
    assert f"working directory is `{host_worktree}`" in prompt
