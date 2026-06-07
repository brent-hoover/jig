"""End-to-end init workflow tests with a fake agent runner.

These tests exercise the ``run_init`` dispatch loop against the real
stores and MCP handlers, replacing the agent spawn with a dispatch
table that simulates each role's side effects before exit. No real
Claude agents are launched.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from jig.init_mcp import (
    handle_arch_set_field,
    handle_pm_propose_profile,
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


def _valid_spec_yaml(name: str = "myproj", capabilities: str = "[]") -> str:
    """Minimal schema-valid spec YAML for tests."""
    return (
        f"name: {name}\n"
        "summary: a project\n"
        f"capabilities: {capabilities}\n"
        "non_goals: []\n"
        f"generated_at: '{datetime.now(timezone.utc).isoformat()}'\n"
        "spec_version: 1\n"
    )


class FakeAgent:
    """Dispatch table keyed on ``(role, ticket_id)``. Each handler
    simulates the agent's side effects against the real stores before
    returning, mimicking an agent that exits cleanly after acting.
    """

    def __init__(self) -> None:
        self._handlers: dict[tuple[str, str], Handler] = {}

    def handle(self, *, role: str, ticket_id: str) -> Callable[[Handler], Handler]:
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
        (proj / "docs" / "brief.md").write_text(
            "# proj\n\nintro\n\n## Planned (committed)\n\n### X\nprose\n"
        )
        await handle_po_finish_brief(
            tickets=ctx.tickets,
            threads=ctx.threads,
            bus=ctx.bus,
            project_path=proj,
            summary="done",
            author="po",
        )

    @agent.handle(role="spec-generator", ticket_id="brief")
    async def _sg(ctx: AgentSpawnContext) -> None:
        await handle_spec_publish(
            tickets=ctx.tickets,
            threads=ctx.threads,
            bus=ctx.bus,
            project_path=ctx.worktree_path,
            yaml_content=_valid_spec_yaml(name="proj"),
            advisory_notes=[],
            author="spec-generator",
        )

    @agent.handle(role="pm", ticket_id="profile")
    async def _pm_profile(ctx: AgentSpawnContext) -> None:
        await handle_pm_propose_profile(
            tickets=ctx.tickets,
            threads=ctx.threads,
            bus=ctx.bus,
            name="small",
            rationale="Single-module CLI, no integrations.",
            author="pm",
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
            tickets=ctx.tickets,
            threads=ctx.threads,
            bus=ctx.bus,
            template_name="python",
            rationale="simple python CLI is enough",
            config={},
            author="sa",
        )

    # Auto-accept prompts: brief_approval=Y, profile_confirm=Y, branch=Y (SA),
    # sa_confirm=Y, init_complete="".
    answers = iter(["Y", "Y", "Y", "Y", ""])
    monkeypatch.setattr("click.prompt", lambda *a, **kw: next(answers))

    # Both modules bind run_agent at import time (`from jig.agent import run_agent`),
    # so patching one alone leaves the other live. Patch both.
    with (
        patch("jig.init_workflow.run_agent", new=agent.run),
        patch("jig.spec_generator.run_agent", new=agent.run),
    ):
        await run_init(name="proj", force=False)

    project = tmp_path / "proj"
    assert (project / "docs" / "brief.md").is_file()
    assert (project / ".jig" / "spec" / "project.structured.yaml").is_file()
    assert (project / ".jig" / "spec" / "architecture.yaml").is_file()
    arch = yaml.safe_load((project / ".jig" / "spec" / "architecture.yaml").read_text())
    assert arch["template"] == "python"
    assert arch["sa_path"] is True
    assert arch["rationale"] == "simple python CLI is enough"


async def test_e2e_direct_path(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    agent = FakeAgent()

    @agent.handle(role="po", ticket_id="brief")
    async def _po(ctx: AgentSpawnContext) -> None:
        proj = ctx.worktree_path
        (proj / "docs" / "brief.md").write_text(
            "# directproj\n\n## Planned (committed)\n\n### X\nprose\n"
        )
        await handle_po_finish_brief(
            tickets=ctx.tickets,
            threads=ctx.threads,
            bus=ctx.bus,
            project_path=proj,
            summary="done",
            author="po",
        )

    @agent.handle(role="spec-generator", ticket_id="brief")
    async def _sg(ctx: AgentSpawnContext) -> None:
        await handle_spec_publish(
            tickets=ctx.tickets,
            threads=ctx.threads,
            bus=ctx.bus,
            project_path=ctx.worktree_path,
            yaml_content=_valid_spec_yaml(name="directproj"),
            advisory_notes=[],
            author="spec-generator",
        )

    @agent.handle(role="pm", ticket_id="profile")
    async def _pm_profile(ctx: AgentSpawnContext) -> None:
        await handle_pm_propose_profile(
            tickets=ctx.tickets,
            threads=ctx.threads,
            bus=ctx.bus,
            name="small",
            rationale="Direct-path test fixture.",
            author="pm",
        )

    # brief_approval=Y, profile_confirm=Y, branch="p" (direct),
    # template pick="1", init_complete="".
    answers = iter(["Y", "Y", "p", "1", ""])
    monkeypatch.setattr("click.prompt", lambda *a, **kw: next(answers))

    with (
        patch("jig.init_workflow.run_agent", new=agent.run),
        patch("jig.spec_generator.run_agent", new=agent.run),
    ):
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
        (proj / "docs" / "brief.md").write_text(
            "# resumeproj\n\n## Planned (committed)\n\n### X\nprose\n"
        )
        await handle_po_finish_brief(
            tickets=ctx.tickets,
            threads=ctx.threads,
            bus=ctx.bus,
            project_path=proj,
            summary="done",
            author="po",
        )

    @agent.handle(role="spec-generator", ticket_id="brief")
    async def _sg(ctx: AgentSpawnContext) -> None:
        await handle_spec_publish(
            tickets=ctx.tickets,
            threads=ctx.threads,
            bus=ctx.bus,
            project_path=ctx.worktree_path,
            yaml_content=_valid_spec_yaml(name="resumeproj"),
            advisory_notes=[],
            author="spec-generator",
        )

    @agent.handle(role="pm", ticket_id="profile")
    async def _pm_profile(ctx: AgentSpawnContext) -> None:
        await handle_pm_propose_profile(
            tickets=ctx.tickets,
            threads=ctx.threads,
            bus=ctx.bus,
            name="small",
            rationale="Resume test fixture.",
            author="pm",
        )

    # First run: brief_approval (call 1 → "Y"), profile_confirm
    # (call 2 → "Y"), then BRANCH_PROMPT (call 3 → KeyboardInterrupt).
    # Second run: resumes at BRANCH_PROMPT (brief approved, spec
    # generated, profile applied), picks direct path + template 1.
    second_answers = iter(["p", "1", ""])
    call_count = {"n": 0}

    def fake_prompt(*a, **kw):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return "Y"  # brief_approval
        if call_count["n"] == 2:
            return "Y"  # profile_confirm
        if call_count["n"] == 3:
            raise KeyboardInterrupt  # BRANCH_PROMPT
        return next(second_answers)

    monkeypatch.setattr("click.prompt", fake_prompt)

    with (
        patch("jig.init_workflow.run_agent", new=agent.run),
        patch("jig.spec_generator.run_agent", new=agent.run),
    ):
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


async def test_story_brief_contains_po_and_specgen_trail(tmp_path: Path, monkeypatch):
    """Smoke test: jig story for the brief and architecture tickets shows
    the expected handoff, spec_generated, advisory note, sa_skipped, and
    scaffold_applied trail after a direct-path init.
    """
    monkeypatch.chdir(tmp_path)
    agent = FakeAgent()

    @agent.handle(role="po", ticket_id="brief")
    async def _po(ctx: AgentSpawnContext) -> None:
        proj = ctx.worktree_path
        (proj / "docs" / "brief.md").write_text(
            "# storyproj\n\n## Planned (committed)\n\n### X\nprose\n"
        )
        await handle_po_finish_brief(
            tickets=ctx.tickets,
            threads=ctx.threads,
            bus=ctx.bus,
            project_path=proj,
            summary="brief drafted",
            author="po",
        )

    @agent.handle(role="spec-generator", ticket_id="brief")
    async def _sg(ctx: AgentSpawnContext) -> None:
        await handle_spec_publish(
            tickets=ctx.tickets,
            threads=ctx.threads,
            bus=ctx.bus,
            project_path=ctx.worktree_path,
            yaml_content=_valid_spec_yaml(name="storyproj"),
            advisory_notes=["watch out for X"],
            author="spec-generator",
        )

    @agent.handle(role="pm", ticket_id="profile")
    async def _pm_profile(ctx: AgentSpawnContext) -> None:
        await handle_pm_propose_profile(
            tickets=ctx.tickets,
            threads=ctx.threads,
            bus=ctx.bus,
            name="small",
            rationale="Story-trail test fixture.",
            author="pm",
        )

    answers = iter(["Y", "Y", "p", "1", ""])
    monkeypatch.setattr("click.prompt", lambda *a, **kw: next(answers))

    with (
        patch("jig.init_workflow.run_agent", new=agent.run),
        patch("jig.spec_generator.run_agent", new=agent.run),
    ):
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


async def test_e2e_resume_after_gap_prompt_picks_R(tmp_path: Path, monkeypatch) -> None:
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
        (proj / "docs" / "brief.md").write_text(body)
        await handle_po_finish_brief(
            tickets=ctx.tickets,
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
                tickets=ctx.tickets,
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
            tickets=ctx.tickets,
            threads=ctx.threads,
            bus=ctx.bus,
            project_path=ctx.worktree_path,
            yaml_content=_valid_spec_yaml(name="gapproj"),
            advisory_notes=[],
            author="spec-generator",
        )

    @agent.handle(role="pm", ticket_id="profile")
    async def _pm_profile(ctx: AgentSpawnContext) -> None:
        await handle_pm_propose_profile(
            tickets=ctx.tickets,
            threads=ctx.threads,
            bus=ctx.bus,
            name="small",
            rationale="Gap-resume test fixture.",
            author="pm",
        )

    # Sequence: brief_approval→Y, gap-prompt→R, brief_approval→Y,
    # profile_confirm→Y, branch→p (direct), template→1, init_complete→"".
    answers = iter(["Y", "R", "Y", "Y", "p", "1", ""])
    monkeypatch.setattr("click.prompt", lambda *a, **kw: next(answers))

    with (
        patch("jig.init_workflow.run_agent", new=agent.run),
        patch("jig.spec_generator.run_agent", new=agent.run),
    ):
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


async def test_e2e_pm_profile_pass_applies_medium(tmp_path: Path, monkeypatch):
    """End-to-end: PM-1 proposes ``medium``, operator confirms, SA
    runs as the medium profile's SA role, and the medium profile's
    workflow set lands in ``.jig/workflows/``.

    This is the integration confidence test for the new PO → PM-1
    → confirm → SA path. Mocks the agent runs but exercises the full
    init state machine + MCP handler + profile_loader.

    Medium stays on the basic ``sa`` role until the v2 init pipeline ships.
    """
    from jig.config import load_config
    from jig.init_workflow import _resolve_sa_role
    from jig.thread import Note

    monkeypatch.chdir(tmp_path)
    agent = FakeAgent()

    @agent.handle(role="po", ticket_id="brief")
    async def _po(ctx: AgentSpawnContext) -> None:
        proj = ctx.worktree_path
        (proj / "docs" / "brief.md").write_text(
            "# medproj\n\n## Planned (committed)\n\n### X\nprose\n"
        )
        await handle_po_finish_brief(
            tickets=ctx.tickets,
            threads=ctx.threads,
            bus=ctx.bus,
            project_path=proj,
            summary="done",
            author="po",
        )

    @agent.handle(role="spec-generator", ticket_id="brief")
    async def _sg(ctx: AgentSpawnContext) -> None:
        await handle_spec_publish(
            tickets=ctx.tickets,
            threads=ctx.threads,
            bus=ctx.bus,
            project_path=ctx.worktree_path,
            yaml_content=_valid_spec_yaml(name="medproj"),
            advisory_notes=[],
            author="spec-generator",
        )

    pm_profile_calls = {"n": 0}

    @agent.handle(role="pm", ticket_id="profile")
    async def _pm_profile(ctx: AgentSpawnContext) -> None:
        pm_profile_calls["n"] += 1
        await handle_pm_propose_profile(
            tickets=ctx.tickets,
            threads=ctx.threads,
            bus=ctx.bus,
            name="medium",
            rationale=(
                "Multiple datastores + external integrations + auth "
                "all named in the brief."
            ),
            author="pm",
        )

    sa_role_seen = {"value": None}

    @agent.handle(role="sa", ticket_id="architecture")
    async def _sa_default(ctx: AgentSpawnContext) -> None:
        sa_role_seen["value"] = ctx.role
        await handle_arch_set_field(
            threads=ctx.threads,
            project_path=ctx.worktree_path,
            path="rationale",
            value="medium-scale arch",
            author="sa",
        )
        await handle_sa_propose_scaffold(
            tickets=ctx.tickets,
            threads=ctx.threads,
            bus=ctx.bus,
            template_name="python",
            rationale="placeholder",
            config={},
            author="sa",
        )

    # Auto-accept all prompts.
    answers = iter(["Y", "Y", "Y", "Y", ""])
    monkeypatch.setattr("click.prompt", lambda *a, **kw: next(answers))

    with (
        patch("jig.init_workflow.run_agent", new=agent.run),
        patch("jig.spec_generator.run_agent", new=agent.run),
    ):
        await run_init(name="medproj", force=False)

    project = tmp_path / "medproj"

    # 1. PM-1 ran once against the profile ticket.
    assert pm_profile_calls["n"] == 1

    # 2. pm_propose_profile Note exists on the profile ticket with
    # the chosen name + rationale.
    threads = ThreadStore(project / ".jig" / "store" / "comments.jsonl")
    await threads.load()
    profile_entries = await threads.for_ticket("profile")
    proposals = [
        e
        for e in profile_entries
        if isinstance(e, Note) and e.payload.get("kind") == "pm_propose_profile"
    ]
    assert len(proposals) == 1
    assert proposals[0].payload["name"] == "medium"

    # 3. Profile applied to config.
    cfg = load_config(project)
    assert cfg.profile.name == "medium"
    # medium stays on basic sa until v2 init pipeline ships
    assert cfg.profile.sa_role == "sa"

    # 4. _resolve_sa_role agrees, and the SA spawn used the medium
    # profile's sa role.
    assert _resolve_sa_role(project) == "sa"
    assert sa_role_seen["value"] == "sa"

    # 5. Medium-profile workflow files copied into .jig/.
    assert (project / ".jig" / "profiles" / "medium.yaml").is_file()
    assert (project / ".jig" / "workflows" / "feature-s-full.yaml").is_file()


async def test_pm_profile_pass_skipped_when_profile_preset(tmp_path: Path, monkeypatch):
    """Eval/auto path: when ``cfg.profile.name`` is non-empty,
    ``classify_resume`` skips ``PM_PROFILE_PASS`` and goes straight
    from SPEC_GENERATION to the architecture-ticket path.

    Tests ``classify_resume`` directly rather than the full
    ``run_init`` loop — the loop would spin if the PM handler is a
    no-op (no proposal posted → re-route to PM_PROFILE_PASS forever).
    The state machine, not the loop, is the unit under test here.
    """
    from datetime import datetime, timezone

    from jig.cli import _apply_profile_at_start
    from jig.init_workflow import ResumeState, classify_resume, create_stub
    from jig.thread import Handoff, SystemEvent
    from jig.ticket import Ticket, WorkType

    project = tmp_path / "preset"
    create_stub(project, name="preset")
    store_dir = project / ".jig" / "store"
    tickets = TicketStore(store_dir / "tickets.jsonl")
    threads = ThreadStore(store_dir / "comments.jsonl")
    await tickets.load()
    await threads.load()

    # Hand-craft the state: brief ticket exists, PO handed off, spec
    # was generated. (Skips actually running PO + spec-gen — we're
    # asserting the classify_resume contract, not the agent loop.)
    await tickets.create(
        Ticket(id="brief", work_type=WorkType.BRIEF, title="b", created_by="cli")
    )
    await threads.post(
        Handoff(
            ticket_id="brief",
            author="po",
            phase="brief",
            outputs=["docs/brief.md", ".jig/spec/project.structured.yaml"],
            summary="done",
        )
    )
    await threads.post(
        SystemEvent(
            ticket_id="brief",
            author="user",
            event_type="brief_approved",
            content="approved",
            timestamp=datetime.now(timezone.utc),
        )
    )
    await threads.post(
        SystemEvent(
            ticket_id="brief",
            author="spec-generator",
            event_type="spec_generated",
            content="generated",
            timestamp=datetime.now(timezone.utc),
        )
    )

    # Sanity: without a profile applied, classify_resume routes to
    # PM_PROFILE_PASS.
    state_before = await classify_resume(
        project_path=project, tickets=tickets, threads=threads
    )
    assert state_before == ResumeState.PM_PROFILE_PASS

    # Pre-apply the small profile (the eval / --profile bypass).
    _apply_profile_at_start(project, "small")

    # After apply: classify_resume falls through to the architecture
    # path — no PM_PROFILE_PASS, no CONFIRM_PROMPT.
    state_after = await classify_resume(
        project_path=project, tickets=tickets, threads=threads
    )
    assert state_after != ResumeState.PM_PROFILE_PASS
    assert state_after != ResumeState.PM_PROFILE_CONFIRM_PROMPT
    assert state_after in {
        ResumeState.BRANCH_PROMPT,  # no architecture ticket yet
        ResumeState.SA_CONVERSATION,
    }
