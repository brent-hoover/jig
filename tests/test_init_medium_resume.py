"""Step 3 tests for the medium L0–L3 init activation in ``jig.init_workflow``:
the ``classify_resume`` medium branch, the dispatch-loop auto-cascade, the
non-medium regression, and the ``sa_mvp`` fail-loud artifact check.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import click
import pytest
import yaml

from jig.config import load_config, save_config
from jig.init_workflow import (
    ResumeState,
    _run_init_resume_loop,
    classify_resume,
    create_stub,
    run_sa_conversation,
)
from jig.profile_loader import apply_profile, load_profile
from jig.store.bus import MessageBus
from jig.store.memory import MemoryStore
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.thread import Handoff


def _medium_project(tmp_path: Path) -> Path:
    target = tmp_path / "proj"
    create_stub(target, name="proj")
    save_config(
        target,
        apply_profile(load_config(target), load_profile("medium", project_path=target)),
    )
    return target


def _small_project(tmp_path: Path) -> Path:
    target = tmp_path / "small"
    create_stub(target, name="small")
    save_config(
        target,
        apply_profile(load_config(target), load_profile("small", project_path=target)),
    )
    return target


async def _stores(target: Path):
    tickets = TicketStore(target / ".jig" / "store" / "tickets.jsonl")
    threads = ThreadStore(target / ".jig" / "store" / "comments.jsonl")
    memory = MemoryStore(target / ".jig" / "store")
    bus = MessageBus(target / ".jig" / "store" / "messages.jsonl")
    for s in (tickets, threads, bus):
        await s.load()
    return tickets, threads, memory, bus


async def _handoff(threads: ThreadStore, ticket_id: str, phase: str) -> None:
    await threads.post(Handoff(ticket_id=ticket_id, author="po", phase=phase))


def _write_suites(target: Path, suite_ids: list[str]) -> None:
    spec_dir = target / ".jig" / "spec"
    spec_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "spec_version": 2,
        "suites": [
            {"id": s, "title": s.title(), "summary": f"s {s}", "capabilities": []}
            for s in suite_ids
        ],
    }
    (spec_dir / "suites.yaml").write_text(yaml.safe_dump(data, sort_keys=False))


# --- classify_resume: medium progression (load-bearing resume test) -----------


async def test_medium_classify_progresses_through_levels(tmp_path: Path) -> None:
    target = _medium_project(tmp_path)
    tickets, threads, _, _ = await _stores(target)

    async def rs() -> ResumeState:
        return await classify_resume(
            project_path=target, tickets=tickets, threads=threads
        )

    # Bare medium project → L0.
    assert await rs() == ResumeState.PO_L0_CONVERSATION
    await _handoff(threads, "project", "po-l1")
    assert await rs() == ResumeState.PO_L1_CONVERSATION
    await _handoff(threads, "discovery", "po-l2")
    assert await rs() == ResumeState.PO_L2_CONVERSATION
    await _handoff(threads, "suites", "po-l3")
    _write_suites(target, ["catalog", "billing"])
    # Two suites pending → L3.
    assert await rs() == ResumeState.PO_L3_CONVERSATION
    await _handoff(threads, "suite-catalog", "sa")
    assert await rs() == ResumeState.PO_L3_CONVERSATION  # billing still pending
    await _handoff(threads, "suite-billing", "sa")
    # All levels done, no architecture ticket → SA (auto-cascade, no branch).
    assert await rs() == ResumeState.SA_CONVERSATION


async def test_medium_classify_none_to_sa_then_done(tmp_path: Path) -> None:
    """The None→SA contract: all levels done routes to SA; once the SA posts
    its arch_finalize handoff the medium path reports ALREADY_DONE (rather than
    re-spawning SA forever)."""
    target = _medium_project(tmp_path)
    tickets, threads, _, _ = await _stores(target)
    _write_suites(target, ["catalog"])
    await _handoff(threads, "project", "po-l1")
    await _handoff(threads, "discovery", "po-l2")
    await _handoff(threads, "suites", "po-l3")
    await _handoff(threads, "suite-catalog", "sa")

    assert (
        await classify_resume(project_path=target, tickets=tickets, threads=threads)
        == ResumeState.SA_CONVERSATION
    )

    # SA runs: architecture ticket + arch_finalize handoff (phase pm).
    from jig.ticket import Ticket, WorkType

    await tickets.create(
        Ticket(
            id="architecture",
            work_type=WorkType.ARCHITECTURE,
            title="A",
            created_by="cli",
        )
    )
    await _handoff(threads, "architecture", "pm")
    assert (
        await classify_resume(project_path=target, tickets=tickets, threads=threads)
        == ResumeState.ALREADY_DONE
    )


# --- medium operator-question handling ----------------------------------------


async def _needs_info_ticket(tickets, threads, ticket_id: str) -> None:
    """Put ``ticket_id`` into needs_info with one open human question."""
    from jig.thread import Question
    from jig.ticket import Ticket, TicketStatus, WorkType

    await tickets.create(
        Ticket(
            id=ticket_id,
            work_type=WorkType.BRIEF,
            title=ticket_id,
            created_by="cli",
            status=TicketStatus.NEEDS_INFO,
        )
    )
    await threads.post(
        Question(
            ticket_id=ticket_id,
            author="po",
            target="any_human",
            question="Which datastore?",
        )
    )


@pytest.mark.parametrize(
    "prereqs, suites, ticket_id",
    [
        ([], [], "project"),  # L0
        ([("project", "po-l1")], [], "discovery"),  # L1
        ([("project", "po-l1"), ("discovery", "po-l2")], [], "suites"),  # L2
        (
            [("project", "po-l1"), ("discovery", "po-l2"), ("suites", "po-l3")],
            ["catalog"],
            "suite-catalog",
        ),  # L3
    ],
)
async def test_medium_classify_routes_to_needs_answer(
    tmp_path: Path, prereqs, suites, ticket_id
) -> None:
    """A medium L0–L3 level with open operator questions routes to
    PO_LEVEL_NEEDS_ANSWER (not back to the PO), at every level."""
    target = _medium_project(tmp_path)
    tickets, threads, _, _ = await _stores(target)
    for tid, phase in prereqs:
        await _handoff(threads, tid, phase)
    if suites:
        _write_suites(target, suites)
    await _needs_info_ticket(tickets, threads, ticket_id)

    assert (
        await classify_resume(project_path=target, tickets=tickets, threads=threads)
        == ResumeState.PO_LEVEL_NEEDS_ANSWER
    )


# --- topology keys off the SA role, not the profile name ----------------------


async def test_custom_named_sa_mvp_profile_routes_to_l0_l3(tmp_path: Path) -> None:
    """The L0–L3 topology decision keys off the resolved SA *role* (sa-mvp), not
    the profile *name* — so a CUSTOM profile (name != 'medium') that sets
    sa_role: sa_mvp still drives the L0–L3 pipeline (and gets the artifact
    preflight), instead of routing to the flat path and failing later."""
    target = tmp_path / "proj"
    create_stub(target, name="proj")
    cfg = load_config(target)
    cfg.profile.name = "big"  # NOT 'medium'
    cfg.profile.sa_role = "sa_mvp"  # but uses the module-producing SA
    save_config(target, cfg)
    tickets, threads, _, _ = await _stores(target)
    assert (
        await classify_resume(project_path=target, tickets=tickets, threads=threads)
        == ResumeState.PO_L0_CONVERSATION
    )


async def test_brief_rejected_for_custom_named_sa_mvp_profile(tmp_path: Path) -> None:
    """--brief is rejected for ANY module-producing-SA profile, not just one
    literally named 'medium' — a persisted custom sa_mvp profile is caught."""
    from jig.init_prompts import AutoPromptHandler
    from jig.init_workflow import run_init

    target = tmp_path / "proj"
    create_stub(target, name="proj")
    cfg = load_config(target)
    cfg.profile.name = "big"
    cfg.profile.sa_role = "sa_mvp"
    save_config(target, cfg)
    brief = tmp_path / "brief.md"
    brief.write_text("# Brief\n")

    with pytest.raises(click.ClickException, match="not supported with the medium"):
        await run_init(
            name=str(target),
            force=False,
            prompts=AutoPromptHandler(),
            brief_file=brief,
            profile_name=None,  # persisted custom profile supplies the topology
        )


async def test_brief_profile_custom_sa_mvp_explicit_rejected(tmp_path: Path) -> None:
    """The explicit eval/unattended path — ``--brief --profile <custom>`` where
    the custom profile (loaded via load_profile) uses sa_role: sa_mvp — must also
    be rejected, not just the persisted-profile path."""
    from jig.init_prompts import AutoPromptHandler
    from jig.init_workflow import run_init

    target = tmp_path / "proj"
    create_stub(target, name="proj")
    profiles_dir = target / ".jig" / "profiles"
    profiles_dir.mkdir(parents=True, exist_ok=True)
    (profiles_dir / "big.yaml").write_text(
        "name: big\n"
        "description: custom profile that uses the module-producing SA\n"
        "sa_role: sa_mvp\n"
        "workflows:\n"
        "  default_by_size: {}\n"
        "  available: []\n"
    )
    brief = tmp_path / "brief.md"
    brief.write_text("# Brief\n")

    with pytest.raises(click.ClickException, match="not supported with the medium"):
        await run_init(
            name=str(target),
            force=False,
            prompts=AutoPromptHandler(),
            brief_file=brief,
            profile_name="big",
        )


# --- non-medium regression ----------------------------------------------------


async def test_small_classify_uses_v1_states(tmp_path: Path) -> None:
    """A small project must keep the v1 flat states — the medium branch is
    skipped entirely."""
    target = _small_project(tmp_path)
    tickets, threads, _, _ = await _stores(target)
    # No brief ticket yet → v1 PO_CONVERSATION (not PO_L0_CONVERSATION).
    assert (
        await classify_resume(project_path=target, tickets=tickets, threads=threads)
        == ResumeState.PO_CONVERSATION
    )


# --- dispatch-loop auto-cascade seam ------------------------------------------


async def test_medium_loop_cascades_l0_to_sa(tmp_path: Path, monkeypatch) -> None:
    """Driving _run_init_resume_loop for a medium project visits the four PO_L*
    arms in order (L3 once per suite) then the SA, with no operator re-entry.

    The spawn helpers are mocked to post the handoff their real finalize would,
    so classify_resume advances each pass."""
    target = _medium_project(tmp_path)
    tickets, threads, memory, bus = await _stores(target)
    _write_suites(target, ["catalog", "billing"])

    order: list[str] = []

    async def _l0(**kw):
        order.append("l0")
        await _handoff(threads, "project", "po-l1")

    async def _l1(**kw):
        order.append("l1")
        await _handoff(threads, "discovery", "po-l2")

    async def _l2(**kw):
        order.append("l2")
        await _handoff(threads, "suites", "po-l3")

    async def _l3(**kw):
        order.append(f"l3:{kw['suite_id']}")
        await _handoff(threads, f"suite-{kw['suite_id']}", "sa")

    async def _sa(**kw):
        order.append("sa")
        from jig.ticket import Ticket, WorkType

        await tickets.create(
            Ticket(
                id="architecture",
                work_type=WorkType.ARCHITECTURE,
                title="A",
                created_by="cli",
            )
        )
        await _handoff(threads, "architecture", "pm")

    monkeypatch.setattr("jig.init_workflow.run_po_l0_conversation", _l0)
    monkeypatch.setattr("jig.init_workflow.run_po_l1_conversation", _l1)
    monkeypatch.setattr("jig.init_workflow.run_po_l2_conversation", _l2)
    monkeypatch.setattr("jig.init_workflow.run_po_l3_conversation", _l3)
    monkeypatch.setattr("jig.init_workflow.run_sa_conversation", _sa)

    await _run_init_resume_loop(
        target=target,
        tickets=tickets,
        threads=threads,
        memory=memory,
        bus=bus,
        prompts=MagicMock(),
        console=MagicMock(),
        brief_file=None,
    )

    assert order == ["l0", "l1", "l2", "l3:catalog", "l3:billing", "sa"]


async def test_medium_loop_answers_questions_before_respawn(
    tmp_path: Path, monkeypatch
) -> None:
    """When the pending level awaits operator answers, the loop surfaces them
    (prompt_and_post_answers on that level's ticket) before respawning the PO,
    then proceeds — it does not loop on the unanswered question."""
    from jig.init_workflow import _open_questions
    from jig.ticket import TicketStatus
    from jig.thread import Answer

    target = _medium_project(tmp_path)
    tickets, threads, memory, bus = await _stores(target)
    _write_suites(target, ["catalog"])
    await _needs_info_ticket(tickets, threads, "project")  # L0 awaits an answer

    answered: list[str] = []

    async def _answer(*, tickets, threads, bus, ticket_id, console=None, prompts=None):
        answered.append(ticket_id)
        for q in await _open_questions(threads, ticket_id):
            await threads.post(
                Answer(
                    ticket_id=ticket_id,
                    author="user",
                    question_id=q.id,
                    text="postgres",
                )
            )
        await tickets.update(ticket_id, status=TicketStatus.IN_PROGRESS)

    order: list[str] = []

    async def _l0(**kw):
        order.append("l0")
        await _handoff(threads, "project", "po-l1")

    async def _l1(**kw):
        order.append("l1")
        await _handoff(threads, "discovery", "po-l2")

    async def _l2(**kw):
        order.append("l2")
        await _handoff(threads, "suites", "po-l3")

    async def _l3(**kw):
        order.append(f"l3:{kw['suite_id']}")
        await _handoff(threads, f"suite-{kw['suite_id']}", "sa")

    async def _sa(**kw):
        order.append("sa")
        from jig.ticket import Ticket, WorkType

        await tickets.create(
            Ticket(
                id="architecture",
                work_type=WorkType.ARCHITECTURE,
                title="A",
                created_by="cli",
            )
        )
        await _handoff(threads, "architecture", "pm")

    monkeypatch.setattr("jig.init_workflow.prompt_and_post_answers", _answer)
    monkeypatch.setattr("jig.init_workflow.run_po_l0_conversation", _l0)
    monkeypatch.setattr("jig.init_workflow.run_po_l1_conversation", _l1)
    monkeypatch.setattr("jig.init_workflow.run_po_l2_conversation", _l2)
    monkeypatch.setattr("jig.init_workflow.run_po_l3_conversation", _l3)
    monkeypatch.setattr("jig.init_workflow.run_sa_conversation", _sa)

    await _run_init_resume_loop(
        target=target,
        tickets=tickets,
        threads=threads,
        memory=memory,
        bus=bus,
        prompts=MagicMock(),
        console=MagicMock(),
        brief_file=None,
    )

    # Answered the L0 ticket's question once, then cascaded through every level.
    assert answered == ["project"]
    assert order == ["l0", "l1", "l2", "l3:catalog", "sa"]


# --- sa_mvp fail-loud ---------------------------------------------------------


async def test_sa_mvp_missing_artifacts_fails_loud(tmp_path: Path) -> None:
    """run_sa_conversation must hard-fail before spawning sa_mvp when an L0–L3
    artifact is missing (here: no suites.yaml / discovery.md at all)."""
    target = _medium_project(tmp_path)
    tickets, threads, memory, bus = await _stores(target)

    with pytest.raises(click.ClickException, match="missing L0–L3 artifacts"):
        await run_sa_conversation(
            project_path=target,
            tickets=tickets,
            threads=threads,
            memory=memory,
            bus=bus,
            console=MagicMock(),
        )


async def test_sa_mvp_guard_matches_kebab_role_id(tmp_path: Path) -> None:
    """The fail-loud guard keys off the RESOLVED role id, not the profile's
    sa_role spelling: a profile using the canonical ``sa-mvp`` (which load_role
    resolves to the same role as the ``sa_mvp`` filename alias) must still get
    the L0–L3 preflight — otherwise the module-producing SA fails opaquely."""
    target = tmp_path / "proj"
    create_stub(target, name="proj")
    cfg = load_config(target)
    cfg.profile.sa_role = "sa-mvp"  # canonical role id, not the filename alias
    save_config(target, cfg)
    tickets, threads, memory, bus = await _stores(target)

    with pytest.raises(click.ClickException, match="missing L0–L3 artifacts"):
        await run_sa_conversation(
            project_path=target,
            tickets=tickets,
            threads=threads,
            memory=memory,
            bus=bus,
            console=MagicMock(),
        )


async def test_sa_mvp_missing_one_suite_spec_fails_loud(tmp_path: Path) -> None:
    """Even with discovery.md + suites.yaml present, a single suite missing its
    spec.structured.yaml is a fail-loud condition."""
    from jig.spec_loader import (
        discovery_path,
        suite_structured_path,
    )

    target = _medium_project(tmp_path)
    tickets, threads, memory, bus = await _stores(target)
    _write_suites(target, ["catalog", "billing"])
    discovery_path(target).parent.mkdir(parents=True, exist_ok=True)
    discovery_path(target).write_text("# discovery\n")
    # Only catalog gets its structured spec; billing is missing.
    catalog_spec = suite_structured_path(target, "catalog")
    catalog_spec.parent.mkdir(parents=True, exist_ok=True)
    catalog_spec.write_text("spec_version: 2\n")

    with pytest.raises(click.ClickException, match="billing"):
        await run_sa_conversation(
            project_path=target,
            tickets=tickets,
            threads=threads,
            memory=memory,
            bus=bus,
            console=MagicMock(),
        )
