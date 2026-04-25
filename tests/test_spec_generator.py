"""Spec-generator one-shot spawn."""
from pathlib import Path

import pytest

from jig.models import RoleConfig
from jig.persistence import save_role
from jig.project import Project, save_project
from jig.spec_generator import run_spec_generator
from jig.store.bus import MessageBus
from jig.store.memory import MemoryStore
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.ticket import Ticket, WorkType


async def _bootstrap_project(tmp_path: Path):
    """Create the .jig directory shape needed for a spawn."""
    (tmp_path / ".jig" / "spec").mkdir(parents=True)
    (tmp_path / ".jig" / "spec" / "project.md").write_text(
        "# p\n\n## Built\n\n- one\n"
    )
    (tmp_path / ".jig" / "roles").mkdir(parents=True, exist_ok=True)
    save_project(
        tmp_path,
        Project(
            id="p",
            name="p",
            path=str(tmp_path),
            language="python",
            package_manager="uv",
        ),
    )
    save_role(tmp_path, RoleConfig(role="spec-generator", phase_prompt="generate"))
    tickets = TicketStore(tmp_path / "tickets.jsonl")
    threads = ThreadStore(tmp_path / "comments.jsonl")
    memory = MemoryStore(tmp_path)
    bus = MessageBus(tmp_path / "messages.jsonl")
    for s in (tickets, threads, memory, bus):
        await s.load()
    return tickets, threads, memory, bus


@pytest.mark.asyncio
async def test_run_spec_generator_invokes_agent(tmp_path: Path, monkeypatch):
    tickets, threads, memory, bus = await _bootstrap_project(tmp_path)
    await tickets.create(
        Ticket(id="brief", work_type=WorkType.BRIEF, title="b", created_by="cli")
    )

    captured: dict = {}

    async def fake_run_agent(ctx, emitter=None):
        captured["ctx"] = ctx
        from jig.agent import RunAgentResult

        return RunAgentResult(status="success", final_text="ok")

    from jig import spec_generator as sg_module

    monkeypatch.setattr(sg_module, "run_agent", fake_run_agent)

    await run_spec_generator(
        project_path=tmp_path,
        tickets=tickets,
        threads=threads,
        memory=memory,
        bus=bus,
    )

    ctx = captured["ctx"]
    assert ctx.role == "spec-generator"
    assert ctx.ticket.id == "brief"
    # init runs against the real project directory, not an isolated worktree
    assert ctx.worktree_path == tmp_path


@pytest.mark.asyncio
async def test_run_spec_generator_missing_brief_raises(tmp_path: Path):
    tickets, threads, memory, bus = await _bootstrap_project(tmp_path)
    with pytest.raises(KeyError, match="brief"):
        await run_spec_generator(
            project_path=tmp_path,
            tickets=tickets,
            threads=threads,
            memory=memory,
            bus=bus,
        )
