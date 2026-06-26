"""Spike 1 (#201) — the pure ``decide()`` state machine.

**Finding: purity holds.** ``decide(state, event) -> (next_state, actions)`` is a
plain synchronous function. The inherently-async work ("spawn agent") is not
performed here — it is *described* by an inert ``SpawnAgent`` dataclass that the
async dispatch shell executes afterward. So the Build engine can be split as
functional-core (this module, pure) / imperative-shell (``dispatch.py``, async)
without ``decide`` ever needing to ``await``.

This is the minimal proof: one real dispatchable transition (``OPEN ->
IN_PROGRESS``, the transition ``find_ready`` feeds today) plus a completion
transition. Epic 4 (Build) bones grows this into the full lifecycle, the
data-driven transition table, and the dispatch/supervisor split.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from jig.ticket import TicketStatus

# ---------------------------------------------------------------------------
# State — an immutable snapshot the machine reasons over.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BuildState:
    """Ticket statuses keyed by id. Immutable: ``decide`` returns a new state,
    never mutates this one. The mapping is defensively copied and frozen at
    construction so a caller-owned dict can't mutate the snapshot out from
    under the purity guarantee."""

    statuses: Mapping[str, TicketStatus]

    def __post_init__(self) -> None:
        object.__setattr__(self, "statuses", MappingProxyType(dict(self.statuses)))


# ---------------------------------------------------------------------------
# Events — inputs to the machine (synthetic in tests, bus-derived in the shell).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TicketReady:
    """A ticket became dispatchable (``OPEN``) and should be worked."""

    ticket_id: str
    role: str


@dataclass(frozen=True)
class AgentCompleted:
    """An agent finished; the ticket reached a terminal status."""

    ticket_id: str
    status: TicketStatus


Event = TicketReady | AgentCompleted


# ---------------------------------------------------------------------------
# Actions — inert descriptions of effects. NOT coroutines. The async shell
# interprets these; the core never performs them.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SpawnAgent:
    """Spawn an agent for ``ticket_id`` in ``role``. The shell does the async
    spawn; this is just the instruction."""

    ticket_id: str
    role: str


Action = SpawnAgent


def _with_status(state: BuildState, ticket_id: str, status: TicketStatus) -> BuildState:
    next_statuses = dict(state.statuses)
    next_statuses[ticket_id] = status
    return BuildState(statuses=next_statuses)


def decide(state: BuildState, event: Event) -> tuple[BuildState, tuple[Action, ...]]:
    """Pure transition: ``(state, event) -> (next_state, actions)``.

    Synchronous and side-effect-free. Returns the next state plus a tuple of
    inert actions for the shell to execute.
    """
    match event:
        case TicketReady(ticket_id, role):
            if state.statuses.get(ticket_id) == TicketStatus.OPEN:
                next_state = _with_status(state, ticket_id, TicketStatus.IN_PROGRESS)
                return next_state, (SpawnAgent(ticket_id=ticket_id, role=role),)
            return state, ()
        case AgentCompleted(ticket_id, status):
            return _with_status(state, ticket_id, status), ()

    return state, ()
