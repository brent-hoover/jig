"""Typed thread-entry models per doc 08.

Phase 4 Task A: replaces the ``Comment``-with-extras envelope that
Phase 3E shipped for Proposal. Every doc-08 entry type gets its own
pydantic model with a typed payload, and the store layer (Task B)
loads them through a discriminated union keyed on ``kind``.

Entry types per doc 08 §Entry types:

* **Question / Answer** — targeted Q&A; asker closes.
* **Objection / Resolution / Waiver** — reviewer gates; objector
  closes (or an authorized waiver bypasses with audit).
* **Decision** — non-obvious choice with rationale; also mirrored as
  a decision record under ``.jig/decisions/`` (Task E).
* **Handoff** — phase completion record; evaluator accepts/rejects.
* **Escalation** — "beyond my scope" signal; blocking.
* **Uncertain** — "route me"; the orchestrator converts to a Question
  or Escalation (Task E).
* **Note** — freeform observation; auto-resolved.
* **Proposal** — owned-artifact change request (Phase 3G).

Plus one non-user-visible folded type:

* **SystemEvent** — legacy ``commit`` / ``phase_run`` / ``status_change``
  records migrated off ``Comment``. Kept as a single subtype because
  these aren't conversations — they're audit trail.

``is_blocking()`` and ``is_resolved()`` encode the gating semantics
from doc 08 §Gating semantics. The dispatch layer (Task H) calls
``has_unresolved_blocking`` on the store to decide whether a phase
can advance.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

from jig.store.models import StoreModel


# Per-field cap on agent-controllable prose. Single thread comments,
# answers, decisions, etc. should fit comfortably in 64 KiB; rejecting
# anything larger bounds a misbehaving (or malicious) agent's ability
# to drive store growth one record at a time. JsonlStore already caps
# the whole serialized record at 1 MiB — this is the per-field belt
# under that suspenders. SEC-I2 in v2-review-findings-security.md.
_MAX_AGENT_PROSE_LEN: int = 65_536


# ---- shared value objects -------------------------------------------------


class DeferredItem(BaseModel):
    """A ``checkpoint_deferred`` item surfaced at handoff time.

    Task G on the checkpoint channel writes these; Task F packages
    them into Handoff entries so the evaluator can review.

    ``id`` is the stable identifier the evaluator references when
    calling ``checkpoint_promote_deferred`` (Phase 5 Task J). Items
    are nested inside Checkpoint records, so the id lives on the
    payload rather than in a top-level JSONL index — the enclosing
    Checkpoint is how we locate it.

    Legacy records written before the id field existed are backfilled
    deterministically by ``jig.store.checkpoints._backfill_deferred_ids``
    as ``{checkpoint_id}:deferred:{index}`` — the uuid4 default here
    only fires for fresh writes, where the generated value is
    serialized into the JSONL and stays stable on reload. Without
    that backfill, Pydantic would regenerate a fresh uuid on every
    load for legacy items and promote-by-id would break across
    orchestrator restarts.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    item: str
    reason: str = ""
    status: Literal["open", "done", "promoted", "accepted"] = "open"
    # Populated when the evaluator promotes to a new ticket (Phase 5
    # Task J wires the auto-creation side). Recorded now so the
    # migration stays read-compatible.
    promoted_ticket_id: str | None = None


# ---- base envelope --------------------------------------------------------


class _ThreadEntryBase(StoreModel):
    """Shared envelope for every thread entry.

    Not a union member itself — concrete subtypes narrow ``kind`` to a
    ``Literal`` so the discriminated union below can route.
    """

    ticket_id: str
    author: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def is_blocking(self) -> bool:
        """Whether this entry blocks phase advancement.

        Default False. Concrete types override for their gating
        semantics per doc 08.
        """
        return False

    def is_resolved(self) -> bool:
        """Whether this entry is closed.

        Default True (note/decision/system-event style). Entries that
        carry a resolution lifecycle (Question / Objection / Handoff)
        override.
        """
        return True


# ---- Question / Answer ----------------------------------------------------


class Question(_ThreadEntryBase):
    """Asks for information or judgment. Resolves when the asker
    (not the answerer) marks it resolved — doc 08 §Gating semantics
    asymmetry rule."""

    kind: Literal["question"] = "question"
    target: str  # actor name, role name, or "any_human"
    question: str = Field(max_length=_MAX_AGENT_PROSE_LEN)
    blocking: bool = False
    resolved_by: str | None = None  # asker's id when closed
    accepted_answer_id: str | None = None  # optional pointer

    def is_blocking(self) -> bool:
        return self.blocking and not self.is_resolved()

    def is_resolved(self) -> bool:
        return self.resolved_by is not None


class Answer(_ThreadEntryBase):
    """Responds to a Question. Does not auto-resolve it."""

    kind: Literal["answer"] = "answer"
    question_id: str
    text: str = Field(max_length=_MAX_AGENT_PROSE_LEN)


# ---- Objection / Resolution / Waiver --------------------------------------


class Objection(_ThreadEntryBase):
    """A reviewer says "this is wrong." Always blocking.

    Closes via ``resolved_by`` (objector accepts a Resolution) or
    ``waived_by`` (an authorized actor overrides via Waiver).
    """

    kind: Literal["objection"] = "objection"
    target_artifact: str  # file path, PR link, prior entry id, etc.
    text: str = Field(max_length=_MAX_AGENT_PROSE_LEN)
    resolved_by: str | None = None
    waived_by: str | None = None

    def is_blocking(self) -> bool:
        return not self.is_resolved()

    def is_resolved(self) -> bool:
        return self.resolved_by is not None or self.waived_by is not None

    def model_post_init(self, __context: object) -> None:  # type: ignore[override]
        # An Objection closes via exactly one path — resolution or
        # waiver — never both. Simultaneously setting both usually
        # means a handler bug writing to the wrong field, and the
        # audit trail becomes ambiguous about how the objection was
        # actually closed.
        if self.resolved_by is not None and self.waived_by is not None:
            raise ValueError(
                "Objection cannot be both resolved_by and waived_by — "
                "these close paths are mutually exclusive"
            )


class Resolution(_ThreadEntryBase):
    """Addresses an Objection with "here's how I fixed it."

    Posting a Resolution does NOT close the Objection — the objector
    must accept via ``thread_accept_resolution`` (Task D).
    """

    kind: Literal["resolution"] = "resolution"
    objection_id: str
    text: str = Field(max_length=_MAX_AGENT_PROSE_LEN)


class Waiver(_ThreadEntryBase):
    """Explicitly overrides an Objection or a check_failure SystemEvent.

    Authorization is enforced at post-time against the author's
    compiled ``capabilities.waivers.can_waive`` set (doc 16 §Capability
    policy). The Waiver itself and its target both stay in the thread
    — the audit trail is the point (doc 08 §Waivers leave
    an audit trail).

    Exactly one of ``objection_id`` / ``check_failure_id`` must be
    set. The historical form waived Objections only; Phase 5 Task E
    added the check-failure form so required-check failures can be
    overridden with the same justified-and-audited flow.
    """

    kind: Literal["waiver"] = "waiver"
    objection_id: str | None = None
    check_failure_id: str | None = None
    justification: str = Field(max_length=_MAX_AGENT_PROSE_LEN)

    def model_post_init(self, __context: object) -> None:  # type: ignore[override]
        set_fields = [
            f
            for f in ("objection_id", "check_failure_id")
            if getattr(self, f) is not None
        ]
        if len(set_fields) != 1:
            raise ValueError(
                "Waiver requires exactly one of objection_id / "
                f"check_failure_id (got: {set_fields or 'none'})"
            )


# ---- Decision / Note / Uncertain / Escalation -----------------------------


class Decision(_ThreadEntryBase):
    """A non-obvious choice made during the work. Auto-resolved.

    Also mirrored to ``.jig/decisions/<ticket>-<seq>.md`` by the MCP
    handler in Task E so the doc-17 decision-record artifact stays
    in sync with the thread entry.
    """

    kind: Literal["decision"] = "decision"
    decision: str = Field(max_length=_MAX_AGENT_PROSE_LEN)
    rationale: str = Field(max_length=_MAX_AGENT_PROSE_LEN)


class Note(_ThreadEntryBase):
    """Freeform observation. Auto-resolved. Replaces the legacy
    ``comment`` kind (Task B migration).

    ``responds_to`` links a Note back at another thread entry it's
    commenting on. Phase 5 Task L uses this for deadlock-nudge
    idempotency — the orchestrator posts at most one nudge per
    blocking entry and keys uniqueness off ``responds_to``. Plain
    human-authored notes leave it ``None``.

    ``payload`` carries structured content for Notes that represent
    machine-generated findings (e.g. spec-generator Gap lists).
    Defaults to an empty dict for plain notes.
    """

    kind: Literal["note"] = "note"
    text: str = Field(max_length=_MAX_AGENT_PROSE_LEN)
    responds_to: str | None = None
    payload: dict[str, object] = Field(default_factory=dict)


class Uncertain(_ThreadEntryBase):
    """Agent doesn't know who should handle this — the orchestrator
    routes (Task E's heuristic). Not blocking itself; the derived
    Question or Escalation carries the gating.
    """

    kind: Literal["uncertain"] = "uncertain"
    details: str


class Escalation(_ThreadEntryBase):
    """Beyond-my-scope signal. Always blocking until a resolver
    acts (Phase 5 wires the auto-routing layer; Phase 4 just records
    and gates).

    ``responds_to`` links this Escalation to a blocking entry the
    orchestrator-as-resolver-of-last-resort escalated on behalf of
    (Phase 5 Task L). Direct agent-posted Escalations leave it
    ``None`` — the field exists so the deadlock sweep can stay
    idempotent without walking log lines.
    """

    kind: Literal["escalation"] = "escalation"
    reason: str  # structured reason code
    details: str  # prose
    target: str = "human"  # role name or "human"
    resolved_by: str | None = None
    responds_to: str | None = None

    def is_blocking(self) -> bool:
        return not self.is_resolved()

    def is_resolved(self) -> bool:
        return self.resolved_by is not None


# ---- Handoff --------------------------------------------------------------


class Handoff(_ThreadEntryBase):
    """Phase completion record. Blocking until the evaluator
    accepts or rejects.

    ``deferred_items`` is the list gathered from the phase's
    checkpoints (Task G assembles it). The evaluator reviews them
    alongside outputs at accept time.
    """

    kind: Literal["handoff"] = "handoff"
    phase: str
    outputs: list[str] = Field(default_factory=list)
    summary: str = ""
    deferred_items: list[DeferredItem] = Field(default_factory=list)
    # Phase 2 — integration checkpoint fields. All optional (defaults []
    # preserve backward compat with existing Handoff records). Downstream
    # phases inspect these so integration drift is visible before runtime.
    new_public_surfaces: list[str] = Field(
        default_factory=list,
        description=(
            "New routes, events, APIs, env vars, or migrations this phase "
            "exposed. Signals to the next phase what new contracts to test."
        ),
    )
    changed_assumptions: list[str] = Field(
        default_factory=list,
        description=(
            "Assumptions that changed vs. what was planned — e.g. a module "
            "changed its owned collection shape mid-phase."
        ),
    )
    required_followups: list[str] = Field(
        default_factory=list,
        description=(
            "Work that must happen before the next phase can succeed — "
            "e.g. 'consumer-driven test for event X must be added'."
        ),
    )
    unknowns: list[str] = Field(
        default_factory=list,
        description=(
            "Open questions not resolved during this phase — carry them "
            "forward so the next phase owner is aware."
        ),
    )
    acceptance_state: Literal["pending", "accepted", "rejected"] = "pending"
    accepted_by: str | None = None
    rejection_reason: str | None = None

    def is_blocking(self) -> bool:
        return not self.is_resolved()

    def is_resolved(self) -> bool:
        return self.acceptance_state != "pending"

    def model_post_init(self, __context: object) -> None:  # type: ignore[override]
        # Keep the resolution fields consistent with acceptance_state
        # at the type level. The MCP handlers already coordinate these
        # on the write path; the invariant here is a safety net for
        # hand-constructed records, direct store.update() paths, and
        # any future caller that flips one field without the other.
        state = self.acceptance_state
        if state == "pending":
            if self.accepted_by is not None or self.rejection_reason is not None:
                raise ValueError(
                    "Handoff(acceptance_state='pending') must have "
                    "accepted_by=None and rejection_reason=None"
                )
        elif state == "accepted":
            if self.accepted_by is None:
                raise ValueError(
                    "Handoff(acceptance_state='accepted') requires accepted_by"
                )
            if self.rejection_reason is not None:
                raise ValueError(
                    "Handoff(acceptance_state='accepted') must not "
                    "carry rejection_reason"
                )
        elif state == "rejected":
            if self.rejection_reason is None:
                raise ValueError(
                    "Handoff(acceptance_state='rejected') requires rejection_reason"
                )


# ---- Proposal (migrated from Phase 3E Comment) ----------------------------


class Proposal(_ThreadEntryBase):
    """Request to change a durable owned artifact. Routed to the
    owning role per the ownership map (Phase 3F).

    Proposal resolves when its state moves off ``pending``. ``refining``
    doesn't count as resolved — the back-and-forth is still open. See
    doc 08 §Proposal / Phase 3G.
    """

    kind: Literal["proposal"] = "proposal"
    target: str  # e.g., "ticket://spec.behaviors"
    section: str | None = None
    change: str | None = None  # YAML fragment or prose
    rationale: str = ""
    state: Literal["pending", "accepted", "rejected", "refining"] = "pending"
    owners: list[str] = Field(default_factory=list)
    parent_id: str | None = None  # resolver entries reference original
    spec_version: int | None = None

    def is_blocking(self) -> bool:
        # Proposals themselves don't block phase advancement — they
        # route to an owner and resolve on their own cadence. If a
        # proposal needs to gate the current phase, the proposer
        # posts an Escalation or a blocking Question.
        return False

    def is_resolved(self) -> bool:
        return self.state in {"accepted", "rejected"}


# ---- SystemEvent (folded legacy kinds) ------------------------------------


class SystemEvent(_ThreadEntryBase):
    """Non-conversational audit records — legacy ``commit`` /
    ``phase_run`` / ``status_change`` kinds migrated off ``Comment``.

    Folded into one type per the Phase 4 plan. Not user-authored in
    the normal sense; the harness writes these in response to
    worktree operations.

    Phase 5 Task D adds ``check_failure`` to the event-type union.
    Posted by the check gate when a required check doesn't pass — one
    entry per failing check per handoff attempt. Agents read these via
    ``read_comments`` and the evaluator prompt surfaces them alongside
    the handoff artifact. Task E will flip ``waived=True`` on these
    entries when an authorized waiver is filed.
    """

    kind: Literal["system_event"] = "system_event"
    event_type: Literal[
        "commit",
        "phase_run",
        "status_change",
        "check_failure",
        "dep_merge_failed",
        "phase_start",
        "phase_end",
        "agent_run",
        "spec_generated",
        "spec_gaps_reported",
        "sa_skipped",
        "scaffold_applied",
        "brief_approved",
        # SF-1: dev-env provisioning failure surfaced as a spawn failure
        # rather than swallowed.
        "provisioning_failed",
        # SF-2 / SF-3: durable signals when reviewer-spawn or auto-commit
        # would otherwise be silent log lines.
        "reviewer_spawn_failed",
        "auto_commit_failed",
        # SF-I1: per-commit hook install failure leaves per-commit review
        # disabled for that ticket; surface so the operator notices.
        "per_commit_hook_install_failed",
        # SF-I4: per-commit runner crashed (still non-blocking) so the
        # operator can distinguish "review passed" from "runner crashed".
        "per_commit_runner_crashed",
        # Review-routing (feature-work/review-routing/plan.md step 8):
        # per-finding routing chose a target phase for the fix loop.
        # Content names the source and target phase + the route reason
        # (target_role / writes-glob / last-touched / unowned-finding /
        # ambiguous-ownership) so operators can see why a particular
        # phase was selected for retry.
        "fix_loop_route",
    ]
    content: str = ""
    commit_sha: str | None = None
    phase_result: Literal["success", "failed", "blocked", "needs_info"] | None = None
    phase_branch: str | None = None
    # ---- check_failure-only fields (Task D) -----------------------------
    # Populated only when ``event_type == "check_failure"``. Left as
    # None/empty for other subtypes so the discriminated-union shape
    # stays flat (adding a nested payload per event_type would churn
    # every existing reader for little gain).
    check_name: str | None = None
    check_severity: Literal["required", "warning"] | None = None
    check_verdict: Literal["pass", "fail", "timeout", "error"] | None = None
    # Tail of the failing check's output, quoted into the evaluator's
    # view. Full output stays in the CheckResult record; the excerpt
    # exists so the thread entry can stand alone in a read_comments
    # dump without joining to the results store.
    excerpt: str = ""
    # Flipped True by ``thread_waive_check`` (Task E). The gate reads
    # this to decide whether a failing check still blocks.
    waived: bool = False
    # Free-form structured data for new event_types (phase_start,
    # phase_end, agent_run). Existing event_types ignore this; the
    # discriminated-union shape stays flat.
    payload: dict[str, object] = Field(default_factory=dict)


# ---- discriminated union --------------------------------------------------


ThreadEntry = Annotated[
    Union[
        Question,
        Answer,
        Objection,
        Resolution,
        Waiver,
        Decision,
        Note,
        Uncertain,
        Escalation,
        Handoff,
        Proposal,
        SystemEvent,
    ],
    Field(discriminator="kind"),
]


# Pydantic needs a concrete wrapper for discriminated-union
# validation from raw dicts at the top level (TypedCollection passes
# a dict to ``model_validate``). The wrapper gives us both
# ``parse_thread_entry(raw)`` for ad-hoc parsing and a holder for
# tests that want to roundtrip through the union.
class _ThreadEntryWrapper(BaseModel):
    entry: ThreadEntry


def parse_thread_entry(raw: dict) -> ThreadEntry:
    """Parse a raw JSONL record into a typed ThreadEntry.

    Unknown ``kind`` values fail loud via pydantic's discriminator
    — no silent fallback. Legacy ``comment`` records are migrated in
    Task B by the store loader, not here.
    """
    return _ThreadEntryWrapper.model_validate({"entry": raw}).entry


# Per-kind extractor for the human-readable text that lived in the
# legacy Comment.content field. Used by prompt_builder, context_resolver,
# and ws_server when rendering a thread to humans or agents.
_ENTRY_CONTENT_MAP = {
    "note": lambda e: e.text,
    "question": lambda e: e.question,
    "answer": lambda e: e.text,
    "decision": lambda e: e.decision,
    "resolution": lambda e: e.text,
    "waiver": lambda e: e.justification,
    "uncertain": lambda e: e.details,
    "escalation": lambda e: e.details,
    "objection": lambda e: e.text,
    "handoff": lambda e: e.summary,
    "proposal": lambda e: e.rationale,
    "system_event": lambda e: e.content,
}


def entry_content(entry: "ThreadEntry") -> str:
    """Extract the human-readable body of a thread entry.

    Each entry kind carries its text on a different field (``text`` on
    Note/Answer/Resolution/Objection, ``decision`` on Decision, etc.).
    Callers use this when flattening a thread for an agent prompt or a
    TUI wire payload.
    """
    return _ENTRY_CONTENT_MAP.get(entry.kind, lambda _e: "")(entry) or ""


__all__ = [
    "Answer",
    "Decision",
    "DeferredItem",
    "Escalation",
    "Handoff",
    "Note",
    "Objection",
    "Proposal",
    "Question",
    "Resolution",
    "SystemEvent",
    "ThreadEntry",
    "Uncertain",
    "Waiver",
    "entry_content",
    "parse_thread_entry",
]
