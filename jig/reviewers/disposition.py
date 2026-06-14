"""Severity-tier disposition policy (Track G Final).

Per ``docs/v2.0/pm-workflow/design.md`` §"Severity tiers and disposition",
each reviewer comment maps to a disposition based on its severity:

- ``critical`` → block: ticket cannot resolve until addressed; the
  Coordinator marks the ticket FAILED with reason ``reviewer-critical``
  so the operator unblocks it explicitly.
- ``important`` → consult SA: the ticket pauses; a Handoff with
  ``phase: "sa-consult"`` lands on the ticket so the SA reviewer agent
  picks it up via the orchestrator's existing dispatch path.
- ``notable`` → no disposition: binary severity — notables never
  block and carry no ack obligation. The orchestrator surfaces them
  as operator-gated proposed issues (``_file_notable_issues``)
  before calling this function; they are ignored here.

The function is mechanical and synchronous-ish (the only async work
is delegating to ``TicketStore`` / ``ThreadStore`` calls). It does
NOT spawn agents directly — the SA-consult Handoff posting triggers
a downstream SA agent via the orchestrator's existing dispatch path.

Out of scope here: re-running reviewers after the operator addresses
critical comments (the cycle controller owns that).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from jig.reviewers.comment import ReviewerComment, Severity
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.thread import Handoff
from jig.ticket import Ticket, TicketStatus

__all__ = [
    "DispositionResult",
    "FAIL_REASON_REVIEWER_CRITICAL",
    "SA_CONSULT_PHASE",
    "apply_severity_disposition",
]

# Reason string the Coordinator stamps on a FAILED ticket when
# critical reviewer comments block resolution. Operator filters on
# this in the TUI to find tickets waiting on review-blocker triage.
FAIL_REASON_REVIEWER_CRITICAL: str = "reviewer-critical"

# Handoff phase tag for SA-consult escalations. The orchestrator's
# existing handoff dispatch path picks this up and routes to the SA
# reviewer agent (no new dispatch logic needed).
SA_CONSULT_PHASE: str = "sa-consult"


class DispositionResult(BaseModel):
    """Outcome of one ``apply_severity_disposition`` call.

    Each list carries the comments that triggered the corresponding
    disposition action so callers can correlate analytics, surface
    the operator-facing summary, and write tests against the actual
    side-effects rather than the function's return value alone.
    """

    model_config = ConfigDict(extra="forbid")

    blocked_by: list[ReviewerComment] = Field(
        default_factory=list,
        description=(
            "Critical comments that flipped the ticket to FAILED with "
            "reason ``reviewer-critical``. Operator unblocks manually."
        ),
    )
    consulted_sa: list[ReviewerComment] = Field(
        default_factory=list,
        description=(
            "Important comments that triggered an SA-consult Handoff "
            "on the ticket. The SA reviewer agent picks the handoff up "
            "via the orchestrator's existing dispatch path."
        ),
    )


async def apply_severity_disposition(
    comments: list[ReviewerComment],
    ticket: Ticket,
    tickets: TicketStore,
    *,
    threads: ThreadStore | None = None,
    author: str = "reviewer-disposition",
) -> DispositionResult:
    """Apply the severity-tier disposition policy to ``comments``.

    Walks ``comments`` once, classifies each by severity, and runs the
    corresponding side effect:

    - Critical → ticket FAILED with reason ``reviewer-critical``.
      Idempotent: re-running on an already-FAILED ticket leaves the
      status alone (we don't want to overwrite a different fail-reason).
    - Important → post a ``Handoff(phase="sa-consult")`` on the ticket
      via ``threads.post`` if a ``ThreadStore`` is provided. Without
      a ThreadStore, the comment is still recorded in
      ``consulted_sa`` for caller introspection but no Handoff is
      posted. The orchestrator's existing handoff dispatch path picks
      up the SA-consult phase and routes to the SA reviewer agent.
    - Notable → ignored here (binary severity — never blocks, no ack
      obligation). The orchestrator files notables as proposed issues
      before calling this function.

    The function returns a ``DispositionResult`` listing every comment
    by branch so callers can correlate analytics and surface a
    structured operator summary. Multiple criticals on one ticket
    only flip the status once (idempotent); multiple importants post
    one Handoff per comment so the SA can address them individually.
    """
    result = DispositionResult()

    for comment in comments:
        severity = comment.severity
        if severity == Severity.CRITICAL.value:
            result.blocked_by.append(comment)
        elif severity == Severity.IMPORTANT.value:
            result.consulted_sa.append(comment)

    # ---- critical → FAILED ------------------------------------------
    if result.blocked_by:
        current = await tickets.get(ticket.id)
        if current is not None and current.status != TicketStatus.FAILED:
            await tickets.update_status(ticket.id, TicketStatus.FAILED)
            # Stamp the fail reason in the ticket's labels so the TUI
            # can filter on it. Use a deterministic prefix so
            # downstream "find review-blocked tickets" queries can
            # walk the labels list rather than parsing prose.
            fresh = await tickets.get(ticket.id)
            if fresh is not None:
                new_labels = list(fresh.labels)
                marker = f"fail:{FAIL_REASON_REVIEWER_CRITICAL}"
                if marker not in new_labels:
                    new_labels.append(marker)
                    await tickets.update(ticket.id, labels=new_labels)

    # ---- important → SA-consult Handoff -----------------------------
    if result.consulted_sa and threads is not None:
        for comment in result.consulted_sa:
            handoff = Handoff(
                ticket_id=ticket.id,
                author=author,
                phase=SA_CONSULT_PHASE,
                outputs=[],
                summary=_summarize_for_handoff(comment),
            )
            await threads.post(handoff)

    return result


def _summarize_for_handoff(comment: ReviewerComment) -> str:
    """Build the Handoff.summary for one important reviewer comment.

    Keep it short — the SA agent reads the whole comment via the
    review-comments store; this string just identifies the trigger.
    """
    anchor = comment.contract_uri or comment.file or "<no anchor>"
    return f"SA consult: {comment.reviewer} flagged {anchor} ({comment.type})"
