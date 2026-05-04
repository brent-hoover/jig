"""Mid-work tier promotion mechanics (Track F Final).

Per ``docs/v2.0/pm-workflow/design.md`` §"Auto-escalation thresholds":

> When auto-escalation fires, the dev agent is force-escalated
> regardless of whether it asked for help — pulled from the ticket,
> partial work preserved in the worktree, escalation report
> assembled, ticket re-dispatched at the next tier (or surfaced to
> operator if already at SA tier).

Track F MVP shipped the analytics + signal detection. Track F Final
wires the actual lifecycle: when auto-escalation fires for a ticket,
the orchestrator promotes the ticket's ``dev_tier`` so the next
dispatch picks up at the higher tier.

**Conservative semantics** (per the v2-plan F Final note): the current
in-flight agent is *not* killed mid-stream. We wait for the run to
finish, persist the new tier, and the next dispatch cycle picks up
the ticket at the new tier. This avoids the race + worktree
re-acquisition logic a real mid-flight kill would need; the
operator-visible behavior is "next time we dispatch this ticket, it
goes at the new tier."

The promotion ladder is fixed:

    standard → senior → sa

A ``sa``-tier ticket that trips a threshold is NOT auto-promoted —
``decide_promotion`` returns ``None`` and the operator (via the
``AutoEscalationTriggered`` event with ``to_tier=operator``) takes
over.
"""
from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from jig.analytics.emitter import EventEmitter
from jig.analytics.events import AutoEscalationTriggered
from jig.auto_escalation import EscalationSignal
from jig.store.tickets import TicketStore

__all__ = [
    "PROMOTION_LADDER",
    "TierPromotion",
    "decide_promotion",
    "promote_ticket_tier",
]


_logger = logging.getLogger(__name__)


# Promotion ladder per design §"Auto-escalation thresholds". The map
# keys are the *current* tier; values are the next tier. ``sa`` has
# no entry — promotion past sa surfaces the situation to the operator
# (the analytics event carries ``to_tier=operator``) but the
# Coordinator does not auto-restart at a higher tier.
PROMOTION_LADDER: dict[str, str] = {
    "standard": "senior",
    "senior": "sa",
}


class TierPromotion(BaseModel):
    """One mid-work tier promotion record.

    Carries the trigger signal + the from/to tier so analytics
    consumers and the operator can correlate the promotion with the
    auto-escalation reasoning. Returned by ``promote_ticket_tier`` so
    callers can record the promotion locally without re-reading the
    ticket store.
    """

    model_config = ConfigDict(extra="forbid")

    ticket_id: str
    from_tier: str
    to_tier: str
    reason: str = Field(
        ...,
        description=(
            "Short prose describing why the promotion fired (typically the "
            "tripped escalation signal's detail string)."
        ),
    )
    triggered_by_signal: EscalationSignal | None = Field(
        default=None,
        description=(
            "The escalation signal that decided the promotion. May be None "
            "for operator-driven promotions; required for auto-promotions."
        ),
    )


def decide_promotion(
    signals: list[EscalationSignal], current_tier: str
) -> str | None:
    """Decide which tier (if any) the ticket should be promoted to.

    Inputs:

    - ``signals`` — the list of escalation signals that just tripped
      for the ticket, as returned by
      ``jig.auto_escalation.check_escalation_signals``.
    - ``current_tier`` — the ticket's current ``dev_tier`` value.

    Returns the next tier per the ``PROMOTION_LADDER``, or ``None`` if:

    - No signals tripped (``signals`` is empty).
    - The ticket is already at the top of the ladder (``sa``) — the
      caller should surface the situation to the operator.
    - The current tier is unknown — defensive; we don't auto-promote
      from a tier we don't recognize.
    """
    if not signals:
        return None
    return PROMOTION_LADDER.get(current_tier)


async def promote_ticket_tier(
    tickets: TicketStore,
    ticket_id: str,
    to_tier: str,
    reason: str,
    *,
    signal: EscalationSignal | None = None,
    emitter: EventEmitter | None = None,
    agent_id: str | None = None,
    turns_at_trip: int = 0,
) -> TierPromotion:
    """Promote a ticket's ``dev_tier`` and emit an analytics event.

    Idempotent in the trivial sense: if the ticket is already at
    ``to_tier``, the update is a no-op (the value matches) but we
    still return a ``TierPromotion`` record so the caller can detect
    "already promoted". Callers wanting strict idempotency should
    consult the returned record's ``from_tier == to_tier`` invariant.

    Emits ``AutoEscalationTriggered`` when both ``emitter`` and
    ``signal`` are supplied — the v2 corpus needs the trigger metric
    + tier delta to build the promotion-rate calibration view. When
    only one of the two is supplied (e.g. operator-driven promotion
    with no signal), the event is skipped — the field is required on
    the analytics schema so we'd produce a malformed event otherwise.
    """
    ticket = await tickets.get(ticket_id)
    if ticket is None:
        raise ValueError(
            f"promote_ticket_tier: ticket {ticket_id!r} not in store"
        )
    from_tier = ticket.dev_tier or "standard"
    if from_tier != to_tier:
        await tickets.update(ticket_id, dev_tier=to_tier)
        _logger.info(
            "promoted ticket %s tier %s → %s (%s)",
            ticket_id,
            from_tier,
            to_tier,
            reason,
        )
    else:
        _logger.debug(
            "ticket %s already at tier %s — no-op promotion",
            ticket_id,
            to_tier,
        )

    if (
        emitter is not None
        and signal is not None
        and from_tier in {"standard", "senior", "sa"}
        and to_tier in {"senior", "sa", "operator"}
        and agent_id is not None
    ):
        from jig.auto_escalation import _SIGNAL_TO_EVENT_TRIP

        trip_signal = _SIGNAL_TO_EVENT_TRIP.get(signal.kind, signal.kind)
        emitter.emit_nowait(
            AutoEscalationTriggered(
                ticket_id=ticket_id,
                agent_id=agent_id,
                from_tier=from_tier,  # type: ignore[arg-type]
                to_tier=to_tier,  # type: ignore[arg-type]
                trip_signal=trip_signal,  # type: ignore[arg-type]
                trip_metric_value=signal.observed_value,
                turns_at_trip=turns_at_trip,
            )
        )

    return TierPromotion(
        ticket_id=ticket_id,
        from_tier=from_tier,
        to_tier=to_tier,
        reason=reason,
        triggered_by_signal=signal,
    )


# Tier literal type used by callers that want stricter typing on the
# return of ``decide_promotion``. The ``Literal`` is loose because the
# ``dev_tier`` field on Ticket is a free-form string for backward
# compatibility with v1 records.
_TierName = Literal["standard", "senior", "sa"]
