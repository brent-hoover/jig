"""End-to-end init workflow tests with a fake agent runner.

These tests exercise the ``run_init`` dispatch loop against the real
stores and MCP handlers, replacing the agent spawn with a dispatch
table that simulates each role's side effects before exit. No real
Claude agents are launched.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from jig.init_mcp import (
    handle_arch_set_field,
    handle_po_finish_brief,
    handle_sa_propose_scaffold,
    handle_spec_publish,
)
from jig.init_workflow import run_init
from jig.models import RoleConfig
from jig.persistence import save_role
from jig.project import Project, save_project
from jig.runtime import AgentSpawnContext


def _seed_project(tmp_path: Path, name: str) -> Path:
    """Create the project directory with config.yaml + role overrides.

    ``run_init`` calls ``load_project``/``load_role`` from inside
    ``run_po_conversation`` etc., so we have to seed both before
    dispatch can succeed. Pre-creating ``.jig/project.yaml`` plus
    ``config.yaml`` puts the directory in ``IN_PROGRESS`` state, which
    ``run_init`` treats the same as ``FRESH``.
    """
    project = tmp_path / name
    project.mkdir()
    (project / ".jig").mkdir()
    (project / ".jig" / "spec").mkdir()
    # Stub project.yaml so classify_directory accepts it as IN_PROGRESS.
    (project / ".jig" / "project.yaml").write_text(
        f"id: {name}\nname: {name}\ncreated_at: 2026-04-24T00:00:00Z\n"
    )
    (project / ".jig" / "spec" / "project.md").write_text(f"# {name}\n")
    save_project(
        project,
        Project(
            id=name,
            name=name,
            path=str(project),
            language="python",
            package_manager="uv",
        ),
    )
    (project / ".jig" / "roles").mkdir(parents=True, exist_ok=True)
    for role in ("po", "sa", "spec-generator"):
        save_role(project, RoleConfig(role=role, phase_prompt=role))
    return project


class FakeAgent:
    """Dispatch table keyed on ``(role, ticket_id)``. Each handler
    simulates the agent's side effects against the real stores before
    returning, mimicking an agent that exits cleanly after acting.
    """

    def __init__(self) -> None:
        self._handlers: dict = {}

    def handle(self, *, role: str, ticket_id: str):
        def deco(fn):
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
    project = _seed_project(tmp_path, "proj")
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

    with patch("jig.init_workflow.run_agent", new=agent.run), \
            patch("jig.spec_generator.run_agent", new=agent.run):
        await run_init(name="proj", force=False)

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
    project = _seed_project(tmp_path, "directproj")
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
    project = _seed_project(tmp_path, "resumeproj")
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

    arch_file = project / ".jig" / "spec" / "architecture.yaml"
    assert arch_file.is_file()
    arch = yaml.safe_load(arch_file.read_text())
    assert arch["sa_path"] is False
    assert "template" in arch
