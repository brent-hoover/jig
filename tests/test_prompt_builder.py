from jig.models import PhaseConfig, RoleConfig
from jig.project import Project
from jig.prompt_builder import SpawnReason, build_initial_prompt
from jig.skill_loader import Skill
from jig.thread import (
    DeferredItem,
    Handoff,
    Note,
    Objection,
    Proposal,
    SystemEvent,
    Waiver,
)
from jig.ticket import Ticket, TicketStatus, WorkType


def _project() -> Project:
    return Project(
        id="p",
        name="p",
        path="/tmp",
        language="python",
        package_manager="uv",
        test_command="uv run pytest",
    )


def _cfg() -> RoleConfig:
    return RoleConfig(
        role="dev", phase_prompt="You are dev.", response_prompt="You answer."
    )


def _ticket() -> Ticket:
    return Ticket(
        work_type=WorkType.REFACTOR,
        title="implement X",
        created_by="orchestrator",
        description="do the thing",
        status=TicketStatus.OPEN,
    )


def test_injection_order() -> None:
    parent = Ticket(
        work_type=WorkType.FEATURE,
        title="parent",
        created_by="user",
        description="overall goal",
    )
    uv_skill = Skill(
        name="uv",
        source_filename="uv.md",
        applies_to={},
        content="# uv\n\nalways uv run",
    )
    prompt = build_initial_prompt(
        role_cfg=_cfg(),
        spawn_reason=SpawnReason.PHASE_PRIMARY,
        ticket=_ticket(),
        parent=parent,
        entries=[],
        memories=["use pytest-asyncio"],
        project=_project(),
        skills=[uv_skill],
        environment_md="## Env\nnever touch legacy/\n",
    )
    assert prompt.index("You are dev.") < prompt.index("## Project Context")
    assert prompt.index("## Project Context") < prompt.index("# uv")
    assert prompt.index("# uv") < prompt.index("## Env")
    assert prompt.index("## Env") < prompt.index("use pytest-asyncio")
    assert prompt.index("use pytest-asyncio") < prompt.index("implement X")
    assert "overall goal" in prompt


def test_qa_responder_uses_response_prompt() -> None:
    prompt = build_initial_prompt(
        role_cfg=_cfg(),
        spawn_reason=SpawnReason.QA_RESPONDER,
        ticket=_ticket(),
        parent=None,
        entries=[],
        memories=[],
        project=_project(),
        skills=[],
        environment_md="",
    )
    assert "You answer." in prompt
    assert "You are dev." not in prompt


def test_qa_responder_falls_back_to_phase_prompt_with_preamble() -> None:
    cfg = RoleConfig(role="dev", phase_prompt="You are dev.")
    prompt = build_initial_prompt(
        role_cfg=cfg,
        spawn_reason=SpawnReason.QA_RESPONDER,
        ticket=_ticket(),
        parent=None,
        entries=[],
        memories=[],
        project=_project(),
        skills=[],
        environment_md="",
    )
    assert "answering a question" in prompt.lower()
    assert "You are dev." in prompt


def test_parent_comments_included() -> None:
    parent = Ticket(
        work_type=WorkType.FEATURE, title="p", created_by="u", description=""
    )
    parent_entries = [
        Note(ticket_id="parent-id", author="spec-writer", text="use redis"),
        Note(ticket_id="parent-id", author="spec-writer", text="index by id"),
    ]
    prompt = build_initial_prompt(
        role_cfg=_cfg(),
        spawn_reason=SpawnReason.PHASE_PRIMARY,
        ticket=_ticket(),
        parent=parent,
        entries=parent_entries,
        memories=[],
        project=_project(),
        skills=[],
        environment_md="",
    )
    assert "use redis" in prompt
    assert "index by id" in prompt


def test_phase_section_interpolates_task_template() -> None:
    phase = PhaseConfig(
        name="implement",
        role="dev",
        task_template="Implement code that passes the tests for: {ticket_title}",
        acceptance_criteria="All tests pass",
    )
    prompt = build_initial_prompt(
        role_cfg=_cfg(),
        spawn_reason=SpawnReason.PHASE_PRIMARY,
        ticket=_ticket(),
        parent=None,
        entries=[],
        memories=[],
        project=_project(),
        skills=[],
        environment_md="",
        phase=phase,
    )
    assert "## Phase: implement" in prompt
    assert "Implement code that passes the tests for: implement X" in prompt
    assert "All tests pass" in prompt


def test_phase_section_accepts_legacy_issue_title_placeholder() -> None:
    phase = PhaseConfig(
        name="spec",
        role="spec",
        task_template="Draft spec for: {issue_title}",
    )
    prompt = build_initial_prompt(
        role_cfg=_cfg(),
        spawn_reason=SpawnReason.PHASE_PRIMARY,
        ticket=_ticket(),
        parent=None,
        entries=[],
        memories=[],
        project=_project(),
        skills=[],
        environment_md="",
        phase=phase,
    )
    assert "Draft spec for: implement X" in prompt


def test_phase_section_unknown_placeholder_left_intact() -> None:
    """Unknown placeholders degrade to visible text rather than crashing."""
    phase = PhaseConfig(name="x", role="dev", task_template="work on {nonexistent}")
    prompt = build_initial_prompt(
        role_cfg=_cfg(),
        spawn_reason=SpawnReason.PHASE_PRIMARY,
        ticket=_ticket(),
        parent=None,
        entries=[],
        memories=[],
        project=_project(),
        skills=[],
        environment_md="",
        phase=phase,
    )
    assert "work on {nonexistent}" in prompt


def test_phase_section_absent_when_phase_none() -> None:
    prompt = build_initial_prompt(
        role_cfg=_cfg(),
        spawn_reason=SpawnReason.PHASE_PRIMARY,
        ticket=_ticket(),
        parent=None,
        entries=[],
        memories=[],
        project=_project(),
        skills=[],
        environment_md="",
    )
    assert "## Phase:" not in prompt
    assert "### Acceptance criteria" not in prompt


# ---- Evaluator prompt composition (Phase 5 Task C/E/J/helper-label) -------
#
# An evaluator agent is spawned on gate-pass to accept or reject a pending
# Handoff. Its prompt must surface:
#
# 1. A distinct role framing — "you are an evaluator" vs the normal work
#    prompt (so a role that pulls double duty as actor + evaluator knows
#    which hat it's wearing).
# 2. The target handoff (phase / summary / outputs / deferred items).
# 3. Structured check results from the CheckResultsStore (latest_batch
#    for the phase) — required gating inputs, not free text.
# 4. Check-failure audit events from the thread — including waived ones —
#    so the evaluator can see historical attempts and which waivers are
#    currently masking failures.
# 5. Active waivers on both check-failures and objections — named with
#    the waiving author and justification.
# 6. Promoted deferred-items on the handoff — already-promoted items show
#    their child ticket id so the evaluator doesn't re-promote them.
# 7. Helper-agent drafts (Notes that `responds_to` a Proposal) — labeled
#    distinctly so they read as context, not human decisions.
# 8. Instructions pointing at `thread_accept_handoff` / `thread_reject_handoff`
#    with the pinned handoff id.


def _handoff_entry(
    *,
    ticket_id: str = "t1",
    phase: str = "implement",
    author: str = "dev",
    summary: str = "",
    outputs: list[str] | None = None,
    deferred_items: list[DeferredItem] | None = None,
) -> Handoff:
    return Handoff(
        ticket_id=ticket_id,
        author=author,
        phase=phase,
        summary=summary,
        outputs=list(outputs or []),
        deferred_items=list(deferred_items or []),
    )


def _eval_ticket() -> Ticket:
    # Match the id expected by the handoff entry helper above.
    return Ticket(
        id="t1",
        work_type=WorkType.FEATURE,
        title="add the thing",
        created_by="orchestrator",
        description="ticket body",
        status=TicketStatus.OPEN,
    )


def test_evaluator_role_framing_distinct_from_phase_primary() -> None:
    """Evaluator prompt frames the agent as an evaluator, not the phase actor.

    Without this, a dev-role agent spawned to evaluate another dev's
    handoff would read its own phase_prompt and think it's doing the
    work. The framing has to make clear which hat is being worn.
    """
    handoff = _handoff_entry()
    prompt = build_initial_prompt(
        role_cfg=_cfg(),
        spawn_reason=SpawnReason.EVALUATOR,
        ticket=_eval_ticket(),
        parent=None,
        entries=[handoff],
        memories=[],
        project=_project(),
        skills=[],
        environment_md="",
        evaluator_bundle={"handoff_id": handoff.id},
    )
    assert "evaluating a handoff" in prompt.lower()
    # The role's own phase_prompt is still present (context) but behind
    # the evaluator framing.
    assert "You are dev." in prompt
    # Instructions point at the accept/reject tools with the pinned id.
    assert "thread_accept_handoff" in prompt
    assert "thread_reject_handoff" in prompt
    assert handoff.id in prompt


def test_evaluator_prompt_surfaces_handoff_record() -> None:
    handoff = _handoff_entry(
        summary="implemented the widget; tests green",
        outputs=["src/widget.py", "tests/test_widget.py"],
    )
    prompt = build_initial_prompt(
        role_cfg=_cfg(),
        spawn_reason=SpawnReason.EVALUATOR,
        ticket=_eval_ticket(),
        parent=None,
        entries=[handoff],
        memories=[],
        project=_project(),
        skills=[],
        environment_md="",
        evaluator_bundle={"handoff_id": handoff.id},
    )
    assert "implement" in prompt  # phase name
    assert "implemented the widget" in prompt
    assert "src/widget.py" in prompt
    assert "tests/test_widget.py" in prompt


def test_evaluator_prompt_surfaces_check_results_structured() -> None:
    """Check results are passed as structured data, not free text.

    The bundle carries serialized CheckResult records; the prompt lists
    them by name + verdict + severity. Failing checks get their output
    excerpt shown so the evaluator can see what broke without a second
    tool call to read_comments.
    """
    handoff = _handoff_entry()
    bundle = {
        "handoff_id": handoff.id,
        "check_results": [
            {
                "check_name": "unit-tests",
                "verdict": "pass",
                "severity": "required",
                "output": "",
            },
            {
                "check_name": "ruff",
                "verdict": "fail",
                "severity": "required",
                "output": "E501 line too long\nF401 unused import",
            },
        ],
    }
    prompt = build_initial_prompt(
        role_cfg=_cfg(),
        spawn_reason=SpawnReason.EVALUATOR,
        ticket=_eval_ticket(),
        parent=None,
        entries=[handoff],
        memories=[],
        project=_project(),
        skills=[],
        environment_md="",
        evaluator_bundle=bundle,
    )
    assert "unit-tests" in prompt
    assert "ruff" in prompt
    # Failing check's excerpt is quoted.
    assert "E501" in prompt
    # Passing check does NOT dump output (quiet success).
    assert prompt.count("```") >= 2  # the fail excerpt is fenced


def test_evaluator_prompt_normalizes_enum_severity() -> None:
    """Orchestrator passes ``r.severity`` (the ``CheckSeverity`` enum)
    straight into the bundle, not ``r.severity.value``. In Python 3.11+
    ``str(CheckSeverity.REQUIRED)`` renders as ``"CheckSeverity.REQUIRED"``
    rather than ``"required"``, so the prompt builder must pull ``.value``
    to avoid leaking the class name into the evaluator prompt."""
    from jig.checks import CheckSeverity

    handoff = _handoff_entry()
    bundle = {
        "handoff_id": handoff.id,
        "check_results": [
            {
                "check_name": "unit-tests",
                "verdict": "pass",
                # The live orchestrator bundle shape — raw enum, not .value.
                "severity": CheckSeverity.REQUIRED,
                "output": "",
            },
            {
                "check_name": "ruff",
                "verdict": "fail",
                "severity": CheckSeverity.WARNING,
                "output": "E501 line too long",
            },
        ],
    }
    prompt = build_initial_prompt(
        role_cfg=_cfg(),
        spawn_reason=SpawnReason.EVALUATOR,
        ticket=_eval_ticket(),
        parent=None,
        entries=[handoff],
        memories=[],
        project=_project(),
        skills=[],
        environment_md="",
        evaluator_bundle=bundle,
    )
    # Enum value rendered, class name never exposed.
    assert "[required]" in prompt
    assert "[warning]" in prompt
    assert "CheckSeverity" not in prompt


def test_evaluator_prompt_surfaces_check_failure_audit_with_waivers() -> None:
    """Historical check_failure events appear alongside current results.

    Waived failures are flagged so the evaluator can see which required
    checks are currently masked by an authorized waiver and who authored
    the waiver.
    """
    handoff = _handoff_entry()
    failure = SystemEvent(
        ticket_id="t1",
        author="harness",
        event_type="check_failure",
        check_name="integration-tests",
        check_severity="required",
        check_verdict="fail",
        excerpt="timeout after 30s",
        waived=True,
    )
    waiver = Waiver(
        ticket_id="t1",
        author="sa",
        check_failure_id=failure.id,
        justification="flaky in CI; tracked as TKT-99",
    )
    prompt = build_initial_prompt(
        role_cfg=_cfg(),
        spawn_reason=SpawnReason.EVALUATOR,
        ticket=_eval_ticket(),
        parent=None,
        entries=[handoff, failure, waiver],
        memories=[],
        project=_project(),
        skills=[],
        environment_md="",
        evaluator_bundle={"handoff_id": handoff.id},
    )
    assert "integration-tests" in prompt
    assert "timeout after 30s" in prompt
    assert "WAIVED" in prompt
    # Active waiver section names the author + justification.
    assert "sa" in prompt
    assert "flaky in CI" in prompt


def test_evaluator_prompt_surfaces_objection_waivers() -> None:
    handoff = _handoff_entry()
    objection = Objection(
        ticket_id="t1",
        author="reviewer",
        target_artifact="src/foo.py",
        text="race condition on startup",
    )
    waiver = Waiver(
        ticket_id="t1",
        author="sa",
        objection_id=objection.id,
        justification="accepted risk; tracked downstream",
    )
    prompt = build_initial_prompt(
        role_cfg=_cfg(),
        spawn_reason=SpawnReason.EVALUATOR,
        ticket=_eval_ticket(),
        parent=None,
        entries=[handoff, objection, waiver],
        memories=[],
        project=_project(),
        skills=[],
        environment_md="",
        evaluator_bundle={"handoff_id": handoff.id},
    )
    assert "accepted risk" in prompt
    assert "race condition" in prompt  # the objection text or a snippet


def test_evaluator_prompt_surfaces_promoted_deferred_items() -> None:
    """Deferred items already promoted to child tickets list the child id.

    Without this, the evaluator might try to re-promote a deferred item
    that's already become its own ticket — Task J's idempotency catches
    that at the tool boundary, but the UX goal is for the evaluator to
    see "this one's handled" at a glance.
    """
    promoted = DeferredItem(
        item="rewrite auth",
        reason="out of scope",
        status="promoted",
        promoted_ticket_id="tkt-42",
    )
    open_item = DeferredItem(
        item="tune cache TTL",
        reason="needs measurement",
        status="open",
    )
    handoff = _handoff_entry(deferred_items=[promoted, open_item])
    prompt = build_initial_prompt(
        role_cfg=_cfg(),
        spawn_reason=SpawnReason.EVALUATOR,
        ticket=_eval_ticket(),
        parent=None,
        entries=[handoff],
        memories=[],
        project=_project(),
        skills=[],
        environment_md="",
        evaluator_bundle={"handoff_id": handoff.id},
    )
    # Promoted item shows the child ticket id.
    assert "tkt-42" in prompt
    assert "rewrite auth" in prompt
    # Open item appears but without a child ticket id.
    assert "tune cache TTL" in prompt


def test_evaluator_prompt_labels_helper_drafts() -> None:
    """Helper-agent Notes that respond to a Proposal are labeled distinctly.

    `jig/helper_spawn.py` (Task N) posts the draft as a Note with
    ``author=<helper_role_name>`` and ``responds_to=<proposal.id>``. The
    evaluator prompt tags these as "helper-agent drafts" so a human
    reviewer reading the prompt knows the text is context, not an
    authoritative human decision.
    """
    handoff = _handoff_entry()
    proposal = Proposal(
        ticket_id="t1",
        author="dev",
        target="ticket://spec.behaviors",
        rationale="tighten the retry policy",
    )
    helper_note = Note(
        ticket_id="t1",
        author="pm",  # the helper role name
        text="Suggest retrying up to 3 times with jitter.",
        responds_to=proposal.id,
    )
    # A plain note (no responds_to) must NOT be labeled as a helper draft.
    plain_note = Note(
        ticket_id="t1",
        author="dev",
        text="FYI: dependency bumped.",
    )
    prompt = build_initial_prompt(
        role_cfg=_cfg(),
        spawn_reason=SpawnReason.EVALUATOR,
        ticket=_eval_ticket(),
        parent=None,
        entries=[handoff, proposal, helper_note, plain_note],
        memories=[],
        project=_project(),
        skills=[],
        environment_md="",
        evaluator_bundle={"handoff_id": handoff.id},
    )
    # Helper-draft label present, scoped to the pm's note.
    assert "helper" in prompt.lower()
    assert "Suggest retrying up to 3 times" in prompt
    # Plain note is still in the normal thread section but not under the
    # helper-draft heading.
    helper_section_start = prompt.lower().find("helper")
    assert helper_section_start != -1
    # The FYI note appears somewhere in the prompt but not between the
    # helper heading and the instructions section (proxy: the helper
    # section is scoped to proposal-responding notes only).
    instructions_start = prompt.find("## Instructions")
    helper_slice = prompt[helper_section_start:instructions_start]
    assert "FYI: dependency bumped" not in helper_slice


def test_evaluator_prompt_without_bundle_still_renders_role_framing() -> None:
    """Missing bundle is a graceful degradation, not a crash.

    Covers the path where `_spawn_evaluator` fails to assemble the
    bundle (e.g., transient store read error). The agent still gets an
    evaluator-framed prompt; the evaluator-specific sections are
    simply absent. The orchestrator logs the degradation.
    """
    prompt = build_initial_prompt(
        role_cfg=_cfg(),
        spawn_reason=SpawnReason.EVALUATOR,
        ticket=_eval_ticket(),
        parent=None,
        entries=[],
        memories=[],
        project=_project(),
        skills=[],
        environment_md="",
        evaluator_bundle=None,
    )
    assert "evaluating a handoff" in prompt.lower()
    # No crash; structured sections are simply absent.
    assert "## Check results" not in prompt
    assert "## Evaluator bundle" not in prompt


def _init_role_cfg(name: str) -> RoleConfig:
    """Init-role configs carry their own situational instructions in
    ``phase_prompt``; the generic block must not be appended."""
    return RoleConfig(role=name, phase_prompt=f"You are {name}.")


def test_po_does_not_get_generic_instructions() -> None:
    """The generic block tells every agent to call ``commit_progress``
    after work and ``update_ticket(... status=resolved)`` to finish.
    PO does neither — its handoff is ``po_finish_brief`` — so seeing
    the generic block confused PO into hunting for a git repo and
    overriding ticket state. Strip it for init roles."""
    prompt = build_initial_prompt(
        role_cfg=_init_role_cfg("po"),
        spawn_reason=SpawnReason.PHASE_PRIMARY,
        ticket=_ticket(),
        parent=None,
        entries=[],
        memories=[],
        project=_project(),
        skills=[],
        environment_md="",
    )
    assert "commit_progress" not in prompt
    assert "update_ticket" not in prompt
    # Generic "## Instructions" header from _instructions_section is
    # what the suppression strips; phase prompts may use other
    # markdown headers, but the literal "## Instructions" header is
    # the one we're guarding against.
    assert "## Instructions\n" not in prompt


def test_sa_does_not_get_generic_instructions() -> None:
    prompt = build_initial_prompt(
        role_cfg=_init_role_cfg("sa"),
        spawn_reason=SpawnReason.PHASE_PRIMARY,
        ticket=_ticket(),
        parent=None,
        entries=[],
        memories=[],
        project=_project(),
        skills=[],
        environment_md="",
    )
    assert "commit_progress" not in prompt
    assert "update_ticket" not in prompt


def test_spec_generator_does_not_get_generic_instructions() -> None:
    prompt = build_initial_prompt(
        role_cfg=_init_role_cfg("spec-generator"),
        spawn_reason=SpawnReason.PHASE_PRIMARY,
        ticket=_ticket(),
        parent=None,
        entries=[],
        memories=[],
        project=_project(),
        skills=[],
        environment_md="",
    )
    assert "commit_progress" not in prompt
    assert "update_ticket" not in prompt


def test_dev_still_gets_generic_instructions() -> None:
    """Operational roles haven't migrated — they still need the
    generic ``commit_progress`` / ``update_ticket`` boilerplate."""
    prompt = build_initial_prompt(
        role_cfg=_cfg(),  # role="dev"
        spawn_reason=SpawnReason.PHASE_PRIMARY,
        ticket=_ticket(),
        parent=None,
        entries=[],
        memories=[],
        project=_project(),
        skills=[],
        environment_md="",
    )
    assert "commit_progress" in prompt
    assert "update_ticket" in prompt
