"""End-to-end init workflow tests with a fake agent runner.

These tests exercise the ``run_init`` dispatch loop against the real
stores and MCP handlers, replacing the agent spawn with a dispatch
table that simulates each role's side effects before exit. No real
Claude agents are launched.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from jig.init_mcp import (
    handle_arch_set_field,
    handle_po_finish_brief,
    handle_sa_propose_scaffold,
    handle_spec_publish,
    handle_spec_report_gaps,
)
from jig.init_workflow import run_init
from jig.runtime import AgentSpawnContext
from jig.spec_generator import Gap
from jig.story import build_story
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.thread import Handoff

Handler = Callable[[AgentSpawnContext], Awaitable[None]]


class FakeAgent:
    """Dispatch table keyed on ``(role, ticket_id)``. Each handler
    simulates the agent's side effects against the real stores before
    returning, mimicking an agent that exits cleanly after acting.
    """

    def __init__(self) -> None:
        self._handlers: dict[tuple[str, str], Handler] = {}

    def handle(
        self, *, role: str, ticket_id: str
    ) -> Callable[[Handler], Handler]:
        def deco(fn: Handler) -> Handler:
            self._handlers[(role, ticket_id)] = fn
            return fn
        return deco

    async def run(self, ctx: AgentSpawnContext, emitter=None) -> None:
        key = (ctx.role, ctx.ticket.id)
        if key in self._handlers:
            await self._handlers[key](ctx)
        # Otherwise no-op — mimics an agent that exits without acting.


async def test_e2e_happy_path_with_sa(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    agent = FakeAgent()

    @agent.handle(role="po", ticket_id="brief")
    async def _po(ctx: AgentSpawnContext) -> None:
        proj = ctx.worktree_path
        (proj / ".jig" / "spec" / "project.md").write_text(
            "# proj\n\nintro\n\n## Planned (committed)\n\n### X\nprose\n"
        )
        await handle_po_finish_brief(
            threads=ctx.threads,
            bus=ctx.bus,
            project_path=proj,
            summary="done",
            author="po",
        )

    @agent.handle(role="spec-generator", ticket_id="brief")
    async def _sg(ctx: AgentSpawnContext) -> None:
        await handle_spec_publish(
            threads=ctx.threads,
            bus=ctx.bus,
            project_path=ctx.worktree_path,
            yaml_content="name: proj\ncapabilities:\n  X: {}\n",
            advisory_notes=[],
            author="spec-generator",
        )

    @agent.handle(role="sa", ticket_id="architecture")
    async def _sa(ctx: AgentSpawnContext) -> None:
        await handle_arch_set_field(
            threads=ctx.threads,
            project_path=ctx.worktree_path,
            path="rationale",
            value="simple python CLI is enough",
            author="sa",
        )
        await handle_sa_propose_scaffold(
            threads=ctx.threads,
            bus=ctx.bus,
            template_name="python",
            rationale="simple python CLI is enough",
            config={},
            author="sa",
        )

    # Auto-accept prompts: branch=Y (SA), confirm=Y (accept proposal).
    answers = iter(["Y", "Y"])
    monkeypatch.setattr("click.prompt", lambda *a, **kw: next(answers))

    # Both modules bind run_agent at import time (`from jig.agent import run_agent`),
    # so patching one alone leaves the other live. Patch both.
    with patch("jig.init_workflow.run_agent", new=agent.run), \
            patch("jig.spec_generator.run_agent", new=agent.run):
        await run_init(name="proj", force=False)

    project = tmp_path / "proj"
    assert (project / ".jig" / "spec" / "project.md").is_file()
    assert (project / ".jig" / "spec" / "project.structured.yaml").is_file()
    assert (project / ".jig" / "spec" / "architecture.yaml").is_file()
    arch = yaml.safe_load(
        (project / ".jig" / "spec" / "architecture.yaml").read_text()
    )
    assert arch["template"] == "python"
    assert arch["sa_path"] is True
    assert arch["rationale"] == "simple python CLI is enough"


async def test_e2e_direct_path(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    agent = FakeAgent()

    @agent.handle(role="po", ticket_id="brief")
    async def _po(ctx: AgentSpawnContext) -> None:
        proj = ctx.worktree_path
        (proj / ".jig" / "spec" / "project.md").write_text(
            "# directproj\n\n## Planned (committed)\n\n### X\nprose\n"
        )
        await handle_po_finish_brief(
            threads=ctx.threads,
            bus=ctx.bus,
            project_path=proj,
            summary="done",
            author="po",
        )

    @agent.handle(role="spec-generator", ticket_id="brief")
    async def _sg(ctx: AgentSpawnContext) -> None:
        await handle_spec_publish(
            threads=ctx.threads,
            bus=ctx.bus,
            project_path=ctx.worktree_path,
            yaml_content="name: directproj\n",
            advisory_notes=[],
            author="spec-generator",
        )

    # branch="p" (direct), template pick="1" (first in sorted list).
    answers = iter(["p", "1"])
    monkeypatch.setattr("click.prompt", lambda *a, **kw: next(answers))

    with patch("jig.init_workflow.run_agent", new=agent.run), \
            patch("jig.spec_generator.run_agent", new=agent.run):
        await run_init(name="directproj", force=False)

    project = tmp_path / "directproj"
    arch_file = project / ".jig" / "spec" / "architecture.yaml"
    assert arch_file.is_file()
    arch = yaml.safe_load(arch_file.read_text())
    assert arch["sa_path"] is False
    assert "rationale" not in arch
    # Sorted templates: ["fastapi", "python"]; pick "1" → "fastapi".
    # Both fastapi and python templates declare ``language: python``.
    assert arch["template"] == "fastapi"
    assert arch["language"] == "python"


async def test_e2e_resume_after_spec_generation(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    agent = FakeAgent()

    @agent.handle(role="po", ticket_id="brief")
    async def _po(ctx: AgentSpawnContext) -> None:
        proj = ctx.worktree_path
        (proj / ".jig" / "spec" / "project.md").write_text(
            "# resumeproj\n\n## Planned (committed)\n\n### X\nprose\n"
        )
        await handle_po_finish_brief(
            threads=ctx.threads,
            bus=ctx.bus,
            project_path=proj,
            summary="done",
            author="po",
        )

    @agent.handle(role="spec-generator", ticket_id="brief")
    async def _sg(ctx: AgentSpawnContext) -> None:
        await handle_spec_publish(
            threads=ctx.threads,
            bus=ctx.bus,
            project_path=ctx.worktree_path,
            yaml_content="name: resumeproj\n",
            advisory_notes=[],
            author="spec-generator",
        )

    # First run: the only click.prompt call before SA dispatch is at
    # the BRANCH_PROMPT. Raise KeyboardInterrupt there to simulate Ctrl-C.
    # Second run: resume from BRANCH_PROMPT, pick direct path + template 1.
    second_answers = iter(["p", "1"])
    call_count = {"n": 0}

    def fake_prompt(*a, **kw):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise KeyboardInterrupt
        return next(second_answers)

    monkeypatch.setattr("click.prompt", fake_prompt)

    with patch("jig.init_workflow.run_agent", new=agent.run), \
            patch("jig.spec_generator.run_agent", new=agent.run):
        with pytest.raises(KeyboardInterrupt):
            await run_init(name="resumeproj", force=False)
        # Second run picks up at BRANCH_PROMPT and lands the scaffold.
        await run_init(name="resumeproj", force=False)

    project = tmp_path / "resumeproj"
    arch_file = project / ".jig" / "spec" / "architecture.yaml"
    assert arch_file.is_file()
    arch = yaml.safe_load(arch_file.read_text())
    assert arch["sa_path"] is False
    assert "template" in arch


async def test_story_brief_contains_po_and_specgen_trail(
    tmp_path: Path, monkeypatch
):
    """Smoke test: jig story for the brief and architecture tickets shows
    the expected handoff, spec_generated, advisory note, sa_skipped, and
    scaffold_applied trail after a direct-path init.
    """
    monkeypatch.chdir(tmp_path)
    agent = FakeAgent()

    @agent.handle(role="po", ticket_id="brief")
    async def _po(ctx: AgentSpawnContext) -> None:
        proj = ctx.worktree_path
        (proj / ".jig" / "spec" / "project.md").write_text(
            "# storyproj\n\n## Planned (committed)\n\n### X\nprose\n"
        )
        await handle_po_finish_brief(
            threads=ctx.threads,
            bus=ctx.bus,
            project_path=proj,
            summary="brief drafted",
            author="po",
        )

    @agent.handle(role="spec-generator", ticket_id="brief")
    async def _sg(ctx: AgentSpawnContext) -> None:
        await handle_spec_publish(
            threads=ctx.threads,
            bus=ctx.bus,
            project_path=ctx.worktree_path,
            yaml_content="name: storyproj\n",
            advisory_notes=["watch out for X"],
            author="spec-generator",
        )

    answers = iter(["p", "1"])
    monkeypatch.setattr("click.prompt", lambda *a, **kw: next(answers))

    with patch("jig.init_workflow.run_agent", new=agent.run), \
            patch("jig.spec_generator.run_agent", new=agent.run):
        await run_init(name="storyproj", force=False)

    project = tmp_path / "storyproj"
    store_dir = project / ".jig" / "store"
    tickets = TicketStore(store_dir / "tickets.jsonl")
    threads = ThreadStore(store_dir / "comments.jsonl")
    await tickets.load()
    await threads.load()

    events = await build_story(
        "brief", project_path=project, threads=threads, tickets=tickets
    )
    kinds = [e.kind for e in events]
    assert "handoff" in kinds
    assert "system_event/spec_generated" in kinds
    assert any("watch out for X" in e.message for e in events)

    arch_events = await build_story(
        "architecture",
        project_path=project,
        threads=threads,
        tickets=tickets,
    )
    arch_kinds = [e.kind for e in arch_events]
    assert "system_event/sa_skipped" in arch_kinds
    assert "system_event/scaffold_applied" in arch_kinds


async def test_e2e_resume_after_gap_prompt_picks_R(
    tmp_path: Path, monkeypatch
) -> None:
    """Picking R at the gap prompt must re-run PO then re-run the
    spec-generator — not loop on the stale gap event.
    """
    monkeypatch.chdir(tmp_path)
    agent = FakeAgent()

    po_calls: dict[str, int] = {"n": 0}
    sg_calls: dict[str, int] = {"n": 0}

    @agent.handle(role="po", ticket_id="brief")
    async def _po(ctx: AgentSpawnContext) -> None:
        po_calls["n"] += 1
        proj = ctx.worktree_path
        body = (
            "# gapproj\n\n## Planned (committed)\n\n"
            f"### X{po_calls['n']}\nprose v{po_calls['n']}\n"
        )
        (proj / ".jig" / "spec" / "project.md").write_text(body)
        await handle_po_finish_brief(
            threads=ctx.threads,
            bus=ctx.bus,
            project_path=proj,
            summary=f"draft v{po_calls['n']}",
            author="po",
        )

    @agent.handle(role="spec-generator", ticket_id="brief")
    async def _sg(ctx: AgentSpawnContext) -> None:
        sg_calls["n"] += 1
        if sg_calls["n"] == 1:
            await handle_spec_report_gaps(
                threads=ctx.threads,
                bus=ctx.bus,
                gaps=[
                    Gap(
                        kind="missing",
                        location="capabilities",
                        description="no capability detail",
                        severity="blocking",
                    )
                ],
                author="spec-generator",
            )
            return
        await handle_spec_publish(
            threads=ctx.threads,
            bus=ctx.bus,
            project_path=ctx.worktree_path,
            yaml_content="name: gapproj\ncapabilities:\n  X2: {}\n",
            advisory_notes=[],
            author="spec-generator",
        )

    # Sequence: gap-prompt → R, branch → p (direct), template → 1.
    answers = iter(["R", "p", "1"])
    monkeypatch.setattr("click.prompt", lambda *a, **kw: next(answers))

    with patch("jig.init_workflow.run_agent", new=agent.run), \
            patch("jig.spec_generator.run_agent", new=agent.run):
        await run_init(name="gapproj", force=False)

    project = tmp_path / "gapproj"
    arch_file = project / ".jig" / "spec" / "architecture.yaml"
    assert arch_file.is_file()
    arch = yaml.safe_load(arch_file.read_text())
    assert arch["sa_path"] is False

    # Brief ticket history must contain TWO Handoff entries — one before
    # the gap prompt, one after PO re-handed-off post-R. That's the proof
    # the resume cycle re-ran PO instead of looping on the stale gap.
    store_dir = project / ".jig" / "store"
    threads = ThreadStore(store_dir / "comments.jsonl")
    await threads.load()
    brief_entries = await threads.for_ticket("brief")
    handoffs = [e for e in brief_entries if isinstance(e, Handoff)]
    assert len(handoffs) == 2
    assert po_calls["n"] == 2
    assert sg_calls["n"] == 2
