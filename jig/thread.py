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

from datetime import datetime, timezone
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

from jig.store.models import StoreModel


# ---- shared value objects -------------------------------------------------


class DeferredItem(BaseModel):
    """A ``checkpoint_deferred`` item surfaced at handoff time.

    Task G on the checkpoint channel writes these; Task F packages
    them into Handoff entries so the evaluator can review.
    """

    item: str
    reason: str = ""
    status: Literal["open", "done", "promoted", "accepted"] = "open"
    # Populated when the evaluator promotes to a new ticket (Phase 5
    # lands the auto-creation side). Recorded now so the migration is
    # read-compatible.
    promoted_ticket_id: str | None = None


# ---- base envelope --------------------------------------------------------


class _ThreadEntryBase(StoreModel):
    """Shared envelope for every thread entry.

    Not a union member itself — concrete subtypes narrow ``kind`` to a
    ``Literal`` so the discriminated union below can route.
    """

    ticket_id: str
    author: str
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

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
    question: str
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
    text: str


# ---- Objection / Resolution / Waiver --------------------------------------


class Objection(_ThreadEntryBase):
    """A reviewer says "this is wrong." Always blocking.

    Closes via ``resolved_by`` (objector accepts a Resolution) or
    ``waived_by`` (an authorized actor overrides via Waiver).
    """

    kind: Literal["objection"] = "objection"
    target_artifact: str  # file path, PR link, prior entry id, etc.
    text: str
    resolved_by: str | None = None
    waived_by: str | None = None

    def is_blocking(self) -> bool:
        return not self.is_resolved()

    def is_resolved(self) -> bool:
        return self.resolved_by is not None or self.waived_by is not None


class Resolution(_ThreadEntryBase):
    """Addresses an Objection with "here's how I fixed it."

    Posting a Resolution does NOT close the Objection — the objector
    must accept via ``thread_accept_resolution`` (Task D).
    """

    kind: Literal["resolution"] = "resolution"
    objection_id: str
    text: str


class Waiver(_ThreadEntryBase):
    """Explicitly overrides an Objection with justification.

    Authorization is enforced at post-time (Task D reads
    ``config.waiver_authority``). The Waiver itself and the original
    Objection both stay in the thread — the audit trail is the point
    (doc 08 §Waivers leave an audit trail).
    """

    kind: Literal["waiver"] = "waiver"
    objection_id: str
    justification: str


# ---- Decision / Note / Uncertain / Escalation -----------------------------


class Decision(_ThreadEntryBase):
    """A non-obvious choice made during the work. Auto-resolved.

    Also mirrored to ``.jig/decisions/<ticket>-<seq>.md`` by the MCP
    handler in Task E so the doc-17 decision-record artifact stays
    in sync with the thread entry.
    """

    kind: Literal["decision"] = "decision"
    decision: str
    rationale: str


class Note(_ThreadEntryBase):
    """Freeform observation. Auto-resolved. Replaces the legacy
    ``comment`` kind (Task B migration)."""

    kind: Literal["note"] = "note"
    text: str


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
    """

    kind: Literal["escalation"] = "escalation"
    reason: str  # structured reason code
    details: str  # prose
    target: str = "human"  # role name or "human"
    resolved_by: str | None = None

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
    acceptance_state: Literal[
        "pending", "accepted", "rejected"
    ] = "pending"
    accepted_by: str | None = None
    rejection_reason: str | None = None

    def is_blocking(self) -> bool:
        return not self.is_resolved()

    def is_resolved(self) -> bool:
        return self.acceptance_state != "pending"


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
    state: Literal["pending", "accepted", "rejected", "refining"] = (
        "pending"
    )
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
    """

    kind: Literal["system_event"] = "system_event"
    event_type: Literal["commit", "phase_run", "status_change"]
    content: str = ""
    commit_sha: str | None = None
    phase_result: Literal[
        "success", "failed", "blocked", "needs_info"
    ] | None = None
    phase_branch: str | None = None


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
    "parse_thread_entry",
]
