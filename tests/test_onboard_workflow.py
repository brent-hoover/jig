"""onboard_workflow: classify_onboard_resume + run_onboard initialization."""

from datetime import datetime, timezone

import click
import pytest
import yaml

import jig.onboard_workflow as onboard_workflow
from jig.config import load_config, save_config
from jig.init_prompts import AutoPromptHandler
from jig.init_workflow import create_stub
from jig.onboard_workflow import (
    OnboardResumeState,
    classify_onboard_resume,
    run_onboard,
)
from jig.store.bus import MessageBus
from jig.store.memory import MemoryStore
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.thread import Handoff, Note, Question, SystemEvent
from jig.ticket import Ticket, TicketStatus, WorkType


@pytest.fixture
async def stores(tmp_path):
    tickets = TicketStore(tmp_path / "tickets.jsonl")
    threads = ThreadStore(tmp_path / "comments.jsonl")
    bus = MessageBus(tmp_path / "messages.jsonl")
    memory = MemoryStore(tmp_path)
    for s in (tickets, threads, memory, bus):
        await s.load()
    return {
        "tickets": tickets,
        "threads": threads,
        "bus": bus,
        "memory": memory,
        "project_path": tmp_path,
    }


async def _classify(stores):
    return await classify_onboard_resume(
        project_path=stores["project_path"],
        tickets=stores["tickets"],
        threads=stores["threads"],
    )


async def _seed_scan_done(stores):
    await stores["tickets"].create(
        Ticket(
            id="onboard-scan",
            work_type=WorkType.ONBOARD_SCAN,
            title="Scan codebase",
            created_by="cli",
        )
    )
    await stores["threads"].post(
        Note(
            ticket_id="onboard-scan",
            author="scanner",
            text="scan complete",
            payload={"kind": "onboard_scan_done"},
        )
    )


async def _seed_brief(stores, *, handoff=False, approved=False, spec_gen=False):
    await stores["tickets"].create(
        Ticket(
            id="brief",
            work_type=WorkType.BRIEF,
            title="Project brief",
            created_by="cli",
        )
    )
    if handoff:
        await stores["threads"].post(
            Handoff(
                ticket_id="brief",
                author="po",
                phase="spec-generator",
                outputs=["docs/brief.md"],
                summary="Current-state brief extracted.",
            )
        )
    if approved:
        await stores["threads"].post(
            SystemEvent(
                ticket_id="brief",
                author="cli",
                event_type="brief_approved",
                content="operator approved brief",
            )
        )
    if spec_gen:
        await stores["threads"].post(
            SystemEvent(
                ticket_id="brief",
                author="spec-generator",
                event_type="spec_generated",
                content="structured spec written",
            )
        )


class TestClassifyOnboardResume:
    async def test_fresh_dir_is_scan_pass(self, stores):
        assert await _classify(stores) == OnboardResumeState.SCAN_PASS

    async def test_scan_ticket_without_done_note_is_scan_pass(self, stores):
        await stores["tickets"].create(
            Ticket(
                id="onboard-scan",
                work_type=WorkType.ONBOARD_SCAN,
                title="Scan codebase",
                created_by="cli",
            )
        )
        assert await _classify(stores) == OnboardResumeState.SCAN_PASS

    async def test_scan_done_is_po_read_pass(self, stores):
        await _seed_scan_done(stores)
        assert await _classify(stores) == OnboardResumeState.PO_READ_PASS

    async def test_brief_without_handoff_is_po_read_pass(self, stores):
        await _seed_scan_done(stores)
        await _seed_brief(stores)
        assert await _classify(stores) == OnboardResumeState.PO_READ_PASS

    async def test_handoff_without_approval_is_po_review(self, stores):
        await _seed_scan_done(stores)
        await _seed_brief(stores, handoff=True)
        assert await _classify(stores) == OnboardResumeState.PO_REVIEW

    async def test_approved_after_handoff_is_spec_pass(self, stores):
        await _seed_scan_done(stores)
        await _seed_brief(stores, handoff=True, approved=True)
        assert await _classify(stores) == OnboardResumeState.SPEC_PASS

    async def test_gaps_after_approval_route_back_to_po_review(self, stores):
        await _seed_scan_done(stores)
        await _seed_brief(stores, handoff=True, approved=True)
        await stores["threads"].post(
            SystemEvent(
                ticket_id="brief",
                author="spec-generator",
                event_type="spec_gaps_reported",
                content="2 gap(s) reported",
            )
        )
        assert await _classify(stores) == OnboardResumeState.PO_REVIEW

    async def test_open_question_is_needs_answer_brief(self, stores):
        await _seed_scan_done(stores)
        await _seed_brief(stores)
        await stores["tickets"].update("brief", status=TicketStatus.NEEDS_INFO)
        await stores["threads"].post(
            Question(
                ticket_id="brief",
                author="po",
                question="What does the batch importer do?",
                target="any_human",
            )
        )
        assert await _classify(stores) == OnboardResumeState.NEEDS_ANSWER_BRIEF

    async def test_spec_generated_without_profile_is_pm_profile_pass(self, stores):
        await _seed_scan_done(stores)
        await _seed_brief(stores, handoff=True, approved=True, spec_gen=True)
        assert await _classify(stores) == OnboardResumeState.PM_PROFILE_PASS

    async def test_profile_proposal_is_confirm_prompt(self, stores):
        await _seed_scan_done(stores)
        await _seed_brief(stores, handoff=True, approved=True, spec_gen=True)
        await stores["tickets"].create(
            Ticket(
                id="profile",
                work_type=WorkType.PROFILE,
                title="Pick project profile",
                created_by="cli",
            )
        )
        await stores["threads"].post(
            Note(
                ticket_id="profile",
                author="pm",
                text="Proposed profile: small",
                payload={
                    "kind": "pm_propose_profile",
                    "name": "small",
                    "rationale": "few modules",
                },
            )
        )
        assert await _classify(stores) == OnboardResumeState.PM_PROFILE_CONFIRM_PROMPT

    async def test_profile_confirmed_is_phase2_pending(self, stores):
        await _seed_scan_done(stores)
        await _seed_brief(stores, handoff=True, approved=True, spec_gen=True)
        create_stub(stores["project_path"], name="proj")
        cfg = load_config(stores["project_path"])
        cfg.profile.name = "small"
        save_config(stores["project_path"], cfg)
        assert await _classify(stores) == OnboardResumeState.PHASE2_PENDING

    async def test_onboard_completed_is_already_done(self, stores):
        create_stub(stores["project_path"], name="proj")
        project_yaml = stores["project_path"] / ".jig" / "project.yaml"
        pdata = yaml.safe_load(project_yaml.read_text())
        pdata["onboard_started_at"] = datetime.now(timezone.utc).isoformat()
        pdata["onboard_completed_at"] = datetime.now(timezone.utc).isoformat()
        project_yaml.write_text(yaml.safe_dump(pdata))
        assert await _classify(stores) == OnboardResumeState.ALREADY_DONE

    async def test_greenfield_completed_is_broken(self, stores):
        create_stub(stores["project_path"], name="proj")
        project_yaml = stores["project_path"] / ".jig" / "project.yaml"
        pdata = yaml.safe_load(project_yaml.read_text())
        pdata["template_applied_at"] = datetime.now(timezone.utc).isoformat()
        project_yaml.write_text(yaml.safe_dump(pdata))
        assert await _classify(stores) == OnboardResumeState.BROKEN


@pytest.fixture
def no_agent_spawn(monkeypatch):
    """Stub the agent spawn; record each (role, ticket) spawned."""
    spawned = []

    async def _record(ctx, *, role_label, console=None, subtitle=None):
        spawned.append((ctx.role, ctx.ticket.id))

    monkeypatch.setattr(onboard_workflow, "_run_agent_with_cli_output", _record)
    return spawned


class TestScanPass:
    async def test_creates_ticket_with_fallback_budget(self, stores, no_agent_spawn):
        create_stub(stores["project_path"], name="proj")
        await onboard_workflow.run_onboard_scan_pass(
            project_path=stores["project_path"],
            tickets=stores["tickets"],
            threads=stores["threads"],
            memory=stores["memory"],
            bus=stores["bus"],
        )
        ticket = await stores["tickets"].get("onboard-scan")
        assert ticket.work_type == WorkType.ONBOARD_SCAN
        assert "at most 400 files" in ticket.description
        assert "Depth limit reached" in ticket.description
        assert no_agent_spawn == [("scanner", "onboard-scan")]

    async def test_small_profile_gets_150_file_ceiling(self, stores, no_agent_spawn):
        create_stub(stores["project_path"], name="proj")
        from jig.profile_loader import apply_profile, load_profile

        cfg = apply_profile(load_config(stores["project_path"]), load_profile("small"))
        save_config(stores["project_path"], cfg)
        await onboard_workflow.run_onboard_scan_pass(
            project_path=stores["project_path"],
            tickets=stores["tickets"],
            threads=stores["threads"],
            memory=stores["memory"],
            bus=stores["bus"],
        )
        ticket = await stores["tickets"].get("onboard-scan")
        assert "at most 150 files" in ticket.description

    async def test_resolved_ticket_reactivated_on_respawn(self, stores, no_agent_spawn):
        create_stub(stores["project_path"], name="proj")
        await stores["tickets"].create(
            Ticket(
                id="onboard-scan",
                work_type=WorkType.ONBOARD_SCAN,
                title="Scan existing codebase",
                created_by="cli",
            )
        )
        await stores["tickets"].update("onboard-scan", status=TicketStatus.RESOLVED)
        await onboard_workflow.run_onboard_scan_pass(
            project_path=stores["project_path"],
            tickets=stores["tickets"],
            threads=stores["threads"],
            memory=stores["memory"],
            bus=stores["bus"],
        )
        ticket = await stores["tickets"].get("onboard-scan")
        assert ticket.status == TicketStatus.IN_PROGRESS


class TestPoReadPass:
    async def test_creates_brief_with_read_mode_and_observations(
        self, stores, no_agent_spawn
    ):
        create_stub(stores["project_path"], name="proj")
        onboard = stores["project_path"] / ".jig" / "onboard"
        onboard.mkdir(parents=True)
        (onboard / "observations.md").write_text(
            "## Project structure\n\nFlask app, 3 modules.\n"
        )
        await onboard_workflow.run_onboard_po_conversation(
            project_path=stores["project_path"],
            tickets=stores["tickets"],
            threads=stores["threads"],
            memory=stores["memory"],
            bus=stores["bus"],
        )
        ticket = await stores["tickets"].get("brief")
        assert ticket.work_type == WorkType.BRIEF
        assert "READ MODE" in ticket.description
        assert "Do NOT invent capabilities" in ticket.description
        assert "Flask app, 3 modules." in ticket.description
        assert no_agent_spawn == [("po", "brief")]

    async def test_missing_observations_fails_loudly(self, stores, no_agent_spawn):
        create_stub(stores["project_path"], name="proj")
        with pytest.raises(click.ClickException, match="observations.md"):
            await onboard_workflow.run_onboard_po_conversation(
                project_path=stores["project_path"],
                tickets=stores["tickets"],
                threads=stores["threads"],
                memory=stores["memory"],
                bus=stores["bus"],
            )
        assert await stores["tickets"].get("brief") is None

    async def test_existing_brief_ticket_reused_and_reactivated(
        self, stores, no_agent_spawn
    ):
        create_stub(stores["project_path"], name="proj")
        await stores["tickets"].create(
            Ticket(
                id="brief",
                work_type=WorkType.BRIEF,
                title="Project brief (onboard read pass)",
                description="original description",
                created_by="cli",
            )
        )
        await stores["tickets"].update("brief", status=TicketStatus.RESOLVED)
        await onboard_workflow.run_onboard_po_conversation(
            project_path=stores["project_path"],
            tickets=stores["tickets"],
            threads=stores["threads"],
            memory=stores["memory"],
            bus=stores["bus"],
        )
        ticket = await stores["tickets"].get("brief")
        assert ticket.description == "original description"
        assert ticket.status == TicketStatus.IN_PROGRESS
        assert no_agent_spawn == [("po", "brief")]


class TestFreshGapNote:
    async def _post_gap(self, stores):
        await stores["threads"].post(
            Note(
                ticket_id="brief",
                author="spec-generator",
                text="Gaps:\n- missing AC",
                payload={
                    "gaps": [
                        {
                            "kind": "missing",
                            "location": "Built/importer",
                            "description": "no acceptance criteria",
                            "severity": "blocking",
                        }
                    ]
                },
            )
        )

    async def test_gaps_after_approval_are_fresh(self, stores):
        await _seed_brief(stores, handoff=True, approved=True)
        await self._post_gap(stores)
        note = await onboard_workflow._fresh_gap_note(stores["threads"])
        assert note is not None
        assert note.payload["gaps"][0]["location"] == "Built/importer"

    async def test_gaps_before_rehandoff_are_stale(self, stores):
        await _seed_brief(stores, handoff=True, approved=True)
        await self._post_gap(stores)
        await stores["threads"].post(
            Handoff(
                ticket_id="brief",
                author="po",
                phase="spec-generator",
                outputs=["docs/brief.md"],
                summary="Gaps addressed.",
            )
        )
        assert await onboard_workflow._fresh_gap_note(stores["threads"]) is None

    async def test_no_gaps_returns_none(self, stores):
        await _seed_brief(stores, handoff=True)
        assert await onboard_workflow._fresh_gap_note(stores["threads"]) is None


class TestPmProfilePass:
    async def test_injects_scanner_recommendation_section(
        self, stores, no_agent_spawn, monkeypatch
    ):
        import jig.init_workflow as init_workflow

        monkeypatch.setattr(
            init_workflow,
            "_run_agent_with_cli_output",
            onboard_workflow._run_agent_with_cli_output,
        )
        create_stub(stores["project_path"], name="proj")
        onboard = stores["project_path"] / ".jig" / "onboard"
        onboard.mkdir(parents=True)
        (onboard / "observations.md").write_text(
            "## Project structure\n\nThree modules.\n\n"
            "## Profile recommendation\n\nsmall — 3 modules, no queues.\n"
        )
        await onboard_workflow.run_onboard_pm_profile_pass(
            project_path=stores["project_path"],
            tickets=stores["tickets"],
            threads=stores["threads"],
            memory=stores["memory"],
            bus=stores["bus"],
        )
        ticket = await stores["tickets"].get("profile")
        assert "ONBOARDED existing" in ticket.description
        assert "small — 3 modules, no queues." in ticket.description
        assert "Three modules." not in ticket.description
        assert no_agent_spawn == [("pm", "profile")]

    async def test_missing_heading_falls_back_to_full_observations(
        self, stores, no_agent_spawn, monkeypatch
    ):
        import jig.init_workflow as init_workflow

        monkeypatch.setattr(
            init_workflow,
            "_run_agent_with_cli_output",
            onboard_workflow._run_agent_with_cli_output,
        )
        create_stub(stores["project_path"], name="proj")
        onboard = stores["project_path"] / ".jig" / "onboard"
        onboard.mkdir(parents=True)
        (onboard / "observations.md").write_text(
            "Free-form prose recommending the small profile.\n"
        )
        await onboard_workflow.run_onboard_pm_profile_pass(
            project_path=stores["project_path"],
            tickets=stores["tickets"],
            threads=stores["threads"],
            memory=stores["memory"],
            bus=stores["bus"],
        )
        ticket = await stores["tickets"].get("profile")
        assert "Free-form prose recommending" in ticket.description


# --- Phase-1 loop harness ----------------------------------------------------
#
# Fake agent behaviors, consumed one per spawn for the PO (so a respawn
# can act differently), plus a scripted PromptHandler for the operator
# gates. Used by TestPhase1Loop to exercise the dispatch arms.


async def _scanner_done(ctx):
    obs = ctx.worktree_path / ".jig" / "onboard" / "observations.md"
    obs.write_text(
        "## Project structure\n\nTwo modules.\n\n"
        "## Profile recommendation\n\nsmall — tiny repo.\n"
    )
    await ctx.threads.post(
        Note(
            ticket_id="onboard-scan",
            author="scanner",
            text="scan complete",
            payload={"kind": "onboard_scan_done"},
        )
    )
    await ctx.tickets.update("onboard-scan", status=TicketStatus.RESOLVED)


async def _po_handoff(ctx):
    await ctx.threads.post(
        Handoff(
            ticket_id="brief",
            author="po",
            phase="spec-generator",
            outputs=["docs/brief.md"],
            summary="Current-state brief extracted.",
        )
    )
    await ctx.tickets.update("brief", status=TicketStatus.RESOLVED)


async def _po_ask_question(ctx):
    await ctx.threads.post(
        Question(
            ticket_id="brief",
            author="po",
            question="How are imports batched?",
            target="any_human",
        )
    )
    await ctx.tickets.update("brief", status=TicketStatus.NEEDS_INFO)


async def _pm_propose_small(ctx):
    await ctx.threads.post(
        Note(
            ticket_id="profile",
            author="pm",
            text="Proposed profile: small",
            payload={
                "kind": "pm_propose_profile",
                "name": "small",
                "rationale": "tiny repo",
            },
        )
    )
    await ctx.tickets.update("profile", status=TicketStatus.RESOLVED)


async def _spec_ok(threads):
    await threads.post(
        SystemEvent(
            ticket_id="brief",
            author="spec-generator",
            event_type="spec_generated",
            content="structured spec written",
        )
    )


async def _spec_gaps(threads):
    await threads.post(
        Note(
            ticket_id="brief",
            author="spec-generator",
            text="Gaps:\n- missing AC",
            payload={
                "gaps": [
                    {
                        "kind": "missing",
                        "location": "Built/importer",
                        "description": "no acceptance criteria",
                        "severity": "blocking",
                    }
                ]
            },
        )
    )
    await threads.post(
        SystemEvent(
            ticket_id="brief",
            author="spec-generator",
            event_type="spec_gaps_reported",
            content="1 gap(s) reported",
        )
    )


class ScriptedPromptHandler(AutoPromptHandler):
    """AutoPromptHandler with scripted overrides for the operator gates.

    ``brief`` / ``profile`` are consumed one per prompt; once exhausted
    the handler falls back to YES (the auto default).
    """

    def __init__(self, *, brief=None, profile=None, answer="Batched nightly."):
        self._brief = list(brief or [])
        self._profile = list(profile or [])
        self._answer = answer

    async def ask_brief_approval(self, *, project_path, console):
        from jig.init_workflow import BriefApprovalChoice

        return self._brief.pop(0) if self._brief else BriefApprovalChoice.YES

    async def ask_profile_confirm(self, *, name, rationale, console):
        from jig.init_workflow import ConfirmChoice

        return self._profile.pop(0) if self._profile else ConfirmChoice.YES

    async def ask_question_answer(self, *, question, index, total, console):
        return self._answer


def _install_phase1_fakes(monkeypatch, *, po_steps, spec_steps):
    """Install fake agent spawns + spec generator. Returns the spawn log
    and a spec-run counter (mutated in place)."""
    import jig.init_workflow as init_workflow
    import jig.spec_generator as spec_generator

    spawned = []
    po_iter = iter(po_steps)
    spec_iter = iter(spec_steps)
    spec_runs = []

    async def fake_spawn(ctx, *, role_label, console=None, subtitle=None):
        spawned.append(ctx.role)
        if ctx.role == "scanner":
            await _scanner_done(ctx)
        elif ctx.role == "po":
            await next(po_iter)(ctx)
        elif ctx.role == "pm":
            await _pm_propose_small(ctx)
        else:  # pragma: no cover - guard against silent role drift
            raise AssertionError(f"unexpected spawn: {ctx.role}")

    async def fake_spec_generator(
        *, project_path, tickets, threads, memory, bus, emitter=None
    ):
        spec_runs.append(1)
        await next(spec_iter)(threads)

    monkeypatch.setattr(onboard_workflow, "_run_agent_with_cli_output", fake_spawn)
    monkeypatch.setattr(init_workflow, "_run_agent_with_cli_output", fake_spawn)
    monkeypatch.setattr(spec_generator, "run_spec_generator", fake_spec_generator)
    return spawned, spec_runs


def _capture_console():
    from io import StringIO

    from rich.console import Console

    buf = StringIO()
    return Console(file=buf, force_terminal=False, width=120), buf


class TestPhase1Loop:
    """Loop-level walks through the dispatch arms with scripted prompts."""

    async def test_full_phase1_flow(self, tmp_path, monkeypatch):
        spawned, spec_runs = _install_phase1_fakes(
            monkeypatch, po_steps=[_po_handoff], spec_steps=[_spec_ok]
        )
        console, buf = _capture_console()

        # The Phase-1 boundary (PHASE2_PENDING) returns cleanly.
        await run_onboard(path=tmp_path, prompts=AutoPromptHandler(), console=console)

        assert spawned == ["scanner", "po", "pm"]
        assert len(spec_runs) == 1
        assert "Phase 1 complete" in buf.getvalue()
        cfg = load_config(tmp_path)
        assert cfg.profile.name == "small"
        tickets = TicketStore(tmp_path / ".jig" / "store" / "tickets.jsonl")
        await tickets.load()
        profile_ticket = await tickets.get("profile")
        assert "small — tiny repo." in profile_ticket.description

    async def test_brief_resume_respawns_po(self, tmp_path, monkeypatch):
        from jig.init_workflow import BriefApprovalChoice

        spawned, spec_runs = _install_phase1_fakes(
            monkeypatch,
            po_steps=[_po_handoff, _po_handoff],
            spec_steps=[_spec_ok],
        )
        console, _ = _capture_console()
        prompts = ScriptedPromptHandler(brief=[BriefApprovalChoice.RESUME])

        await run_onboard(path=tmp_path, prompts=prompts, console=console)

        assert spawned == ["scanner", "po", "po", "pm"]
        assert len(spec_runs) == 1

    async def test_brief_rejection_exits_with_state_saved(self, tmp_path, monkeypatch):
        from jig.init_workflow import BriefApprovalChoice

        spawned, spec_runs = _install_phase1_fakes(
            monkeypatch, po_steps=[_po_handoff], spec_steps=[_spec_ok]
        )
        console, buf = _capture_console()
        prompts = ScriptedPromptHandler(brief=[BriefApprovalChoice.NO])

        await run_onboard(path=tmp_path, prompts=prompts, console=console)

        assert spawned == ["scanner", "po"]
        assert len(spec_runs) == 0
        assert "Brief not approved" in buf.getvalue()

    async def test_spec_gaps_rendered_at_review_gate(self, tmp_path, monkeypatch):
        spawned, spec_runs = _install_phase1_fakes(
            monkeypatch,
            po_steps=[_po_handoff],
            spec_steps=[_spec_gaps, _spec_ok],
        )
        console, buf = _capture_console()

        await run_onboard(path=tmp_path, prompts=AutoPromptHandler(), console=console)

        # gaps → back to the review gate (gap text shown) → re-approve →
        # second spec run succeeds.
        assert len(spec_runs) == 2
        out = buf.getvalue()
        assert "Spec generation reported gaps" in out
        assert "Built/importer: no acceptance criteria" in out

    async def test_open_question_routed_through_answers(self, tmp_path, monkeypatch):
        from jig.thread import Answer

        spawned, spec_runs = _install_phase1_fakes(
            monkeypatch,
            po_steps=[_po_ask_question, _po_handoff],
            spec_steps=[_spec_ok],
        )
        console, _ = _capture_console()
        prompts = ScriptedPromptHandler(answer="Imports are batched nightly.")

        await run_onboard(path=tmp_path, prompts=prompts, console=console)

        assert spawned == ["scanner", "po", "po", "pm"]
        threads = ThreadStore(tmp_path / ".jig" / "store" / "comments.jsonl")
        await threads.load()
        entries = await threads.for_ticket("brief")
        answers = [e for e in entries if isinstance(e, Answer)]
        assert len(answers) == 1
        assert answers[0].text == "Imports are batched nightly."

    async def test_profile_swap_applies_other_profile(self, tmp_path, monkeypatch):
        from jig.init_workflow import ConfirmChoice

        spawned, _ = _install_phase1_fakes(
            monkeypatch, po_steps=[_po_handoff], spec_steps=[_spec_ok]
        )
        console, _ = _capture_console()
        prompts = ScriptedPromptHandler(profile=[ConfirmChoice.SWAP])

        await run_onboard(path=tmp_path, prompts=prompts, console=console)

        # PM proposed small; SWAP flips to medium.
        cfg = load_config(tmp_path)
        assert cfg.profile.name == "medium"

    async def test_profile_rejection_exits_without_applying(
        self, tmp_path, monkeypatch
    ):
        from jig.init_workflow import ConfirmChoice

        spawned, _ = _install_phase1_fakes(
            monkeypatch, po_steps=[_po_handoff], spec_steps=[_spec_ok]
        )
        console, buf = _capture_console()
        prompts = ScriptedPromptHandler(profile=[ConfirmChoice.NO])

        await run_onboard(path=tmp_path, prompts=prompts, console=console)

        assert "Profile not approved" in buf.getvalue()
        cfg = load_config(tmp_path)
        assert cfg.profile.name == ""


@pytest.fixture
def noop_loop(monkeypatch):
    """Stub the resume loop so initialization tests don't spawn agents."""

    async def _noop(**kwargs):
        return None

    monkeypatch.setattr(onboard_workflow, "_run_onboard_resume_loop", _noop)


class TestRunOnboardInit:
    async def test_creates_stub_and_onboard_state(self, tmp_path, noop_loop):
        await run_onboard(path=tmp_path, prompts=AutoPromptHandler())
        pdata = yaml.safe_load((tmp_path / ".jig" / "project.yaml").read_text())
        assert pdata["onboard_started_at"]
        assert "template_applied_at" not in pdata
        assert (tmp_path / ".jig" / "onboard").is_dir()
        assert (tmp_path / ".jig" / "config.yaml").is_file()

    async def test_brief_lands_as_desired_state(self, tmp_path, noop_loop):
        brief = tmp_path / "wish.md"
        brief.write_text("# Desired\n\nAdd exports.\n")
        await run_onboard(path=tmp_path, brief_file=brief, prompts=AutoPromptHandler())
        desired = tmp_path / ".jig" / "onboard" / "desired-state.md"
        assert desired.read_text() == "# Desired\n\nAdd exports.\n"

    async def test_resume_preserves_onboard_started_at(self, tmp_path, noop_loop):
        await run_onboard(path=tmp_path, prompts=AutoPromptHandler())
        pdata = yaml.safe_load((tmp_path / ".jig" / "project.yaml").read_text())
        first = pdata["onboard_started_at"]
        await run_onboard(path=tmp_path, prompts=AutoPromptHandler())
        pdata = yaml.safe_load((tmp_path / ".jig" / "project.yaml").read_text())
        assert pdata["onboard_started_at"] == first

    async def test_force_clears_and_restores_operator_yamls(self, tmp_path, noop_loop):
        await run_onboard(path=tmp_path, prompts=AutoPromptHandler())
        profiles = tmp_path / ".jig" / "profiles"
        workflows = tmp_path / ".jig" / "workflows"
        profiles.mkdir(parents=True)
        workflows.mkdir(parents=True)
        (profiles / "custom.yaml").write_text("name: custom\n")
        (workflows / "special.yaml").write_text("name: special\n")
        marker = tmp_path / ".jig" / "onboard" / "desired-state.md"
        marker.write_text("stale desired state\n")

        await run_onboard(path=tmp_path, force=True, prompts=AutoPromptHandler())

        assert not marker.exists()
        assert (profiles / "custom.yaml").read_text() == "name: custom\n"
        assert (workflows / "special.yaml").read_text() == "name: special\n"
        pdata = yaml.safe_load((tmp_path / ".jig" / "project.yaml").read_text())
        assert pdata["onboard_started_at"]

    async def test_rejects_greenfield_in_progress_without_force(
        self, tmp_path, noop_loop
    ):
        create_stub(tmp_path, name="proj")
        with pytest.raises(click.ClickException, match="in-progress `jig init`"):
            await run_onboard(path=tmp_path, prompts=AutoPromptHandler())

    async def test_rejects_greenfield_completed_without_force(
        self, tmp_path, noop_loop
    ):
        create_stub(tmp_path, name="proj")
        project_yaml = tmp_path / ".jig" / "project.yaml"
        pdata = yaml.safe_load(project_yaml.read_text())
        pdata["template_applied_at"] = datetime.now(timezone.utc).isoformat()
        project_yaml.write_text(yaml.safe_dump(pdata))
        with pytest.raises(click.ClickException, match="jig init"):
            await run_onboard(path=tmp_path, prompts=AutoPromptHandler())

    async def test_resumes_onboard_in_progress_without_force(self, tmp_path, noop_loop):
        await run_onboard(path=tmp_path, prompts=AutoPromptHandler())
        # Second run on an in-progress onboard project must not raise.
        await run_onboard(path=tmp_path, prompts=AutoPromptHandler())

    async def test_profile_bypass_applies_named_profile(self, tmp_path, noop_loop):
        await run_onboard(
            path=tmp_path, profile_name="small", prompts=AutoPromptHandler()
        )
        cfg = load_config(tmp_path)
        assert cfg.profile.name == "small"

    async def test_invalid_profile_rejected_before_state_mutation(
        self, tmp_path, noop_loop
    ):
        with pytest.raises(click.ClickException, match="not found"):
            await run_onboard(
                path=tmp_path, profile_name="smal", prompts=AutoPromptHandler()
            )
        assert not (tmp_path / ".jig").exists()

    async def test_dot_path_resolves_project_name(
        self, tmp_path, noop_loop, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        from pathlib import Path

        await run_onboard(path=Path("."), prompts=AutoPromptHandler())
        pdata = yaml.safe_load((tmp_path / ".jig" / "project.yaml").read_text())
        assert pdata["name"] == tmp_path.name
