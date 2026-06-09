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

    async def test_profile_confirmed_raises_not_implemented(self, stores):
        await _seed_scan_done(stores)
        await _seed_brief(stores, handoff=True, approved=True, spec_gen=True)
        create_stub(stores["project_path"], name="proj")
        cfg = load_config(stores["project_path"])
        cfg.profile.name = "small"
        save_config(stores["project_path"], cfg)
        with pytest.raises(NotImplementedError, match="sa-architect Phase 2"):
            await _classify(stores)

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

    async def test_force_clears_and_restores_operator_yamls(
        self, tmp_path, noop_loop
    ):
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

    async def test_resumes_onboard_in_progress_without_force(
        self, tmp_path, noop_loop
    ):
        await run_onboard(path=tmp_path, prompts=AutoPromptHandler())
        # Second run on an in-progress onboard project must not raise.
        await run_onboard(path=tmp_path, prompts=AutoPromptHandler())

    async def test_profile_bypass_applies_named_profile(self, tmp_path, noop_loop):
        await run_onboard(
            path=tmp_path, profile_name="small", prompts=AutoPromptHandler()
        )
        cfg = load_config(tmp_path)
        assert cfg.profile.name == "small"
