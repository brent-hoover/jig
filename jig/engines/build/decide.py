"""The Build engine's pure ``decide()`` state machine.

``decide(state, event) -> (next_state, actions)`` is a plain synchronous,
side-effect-free function (proven in Spike 1, #201). The inherently-async work
(spawn agent, merge worktree, …) is *described* by inert action dataclasses that
the async dispatch shell (``dispatch.py``) executes afterward. ``decide`` never
awaits and never mutates input state — functional-core / imperative-shell.

The machine is **data-driven**: transitions live in the ``_TRANSITIONS`` table
keyed by ``(current status, event type)``, not in if/else chains. Bones covers
the happy path (``OPEN → IN_PROGRESS → merge → RESOLVED``); MVP/Final grow the
table with sad-path transitions (blocked, needs-info, review-failed, replan).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType

from jig.ticket import TicketStatus

_log = logging.getLogger(__name__)


class BuildPhase(str, Enum):
    """Build-engine-internal sub-states that have no persisted ``TicketStatus``.

    ``MERGING`` distinguishes "agent succeeded, worktree merge dispatched,
    awaiting the result" from plain ``IN_PROGRESS`` (agent still working). Making
    it a distinct state keeps the transitions sound: a duplicate ``AgentSucceeded``
    can't dispatch a second merge, and a stray ``WorktreeMerged`` can't resolve a
    ticket that never entered merge-pending.
    """

    MERGING = "merging"


# An engine state is either a persisted ticket status or an internal phase.
EngineState = TicketStatus | BuildPhase


# ---------------------------------------------------------------------------
# State — an immutable snapshot the machine reasons over.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BuildState:
    """Engine state per ticket id (a ``TicketStatus`` or internal ``BuildPhase``).
    Immutable: ``decide`` returns a new state, never mutates this one. The mapping
    is defensively copied and frozen at construction so a caller-owned dict can't
    mutate the snapshot out from under the purity guarantee."""

    statuses: Mapping[str, EngineState]

    def __post_init__(self) -> None:
        object.__setattr__(self, "statuses", MappingProxyType(dict(self.statuses)))


# ---------------------------------------------------------------------------
# Events — inputs to the machine (synthetic in tests, bus-derived in the shell).
# Every event carries ``ticket_id`` so the engine can look up current status.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TicketReady:
    """A ticket became dispatchable (``OPEN``) and should be worked."""

    ticket_id: str
    role: str


@dataclass(frozen=True)
class AgentSucceeded:
    """The agent finished its work successfully; the ticket is ready to merge."""

    ticket_id: str


@dataclass(frozen=True)
class WorktreeMerged:
    """The ticket's worktree branch merged cleanly into the default branch."""

    ticket_id: str


@dataclass(frozen=True)
class AgentCompleted:
    """An agent finished and the ticket reached an explicit terminal status
    (used for non-happy-path terminal transitions; the happy path uses
    ``AgentSucceeded`` + ``WorktreeMerged``)."""

    ticket_id: str
    status: TicketStatus


Event = TicketReady | AgentSucceeded | WorktreeMerged | AgentCompleted


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


@dataclass(frozen=True)
class MergeWorktree:
    """Merge the ticket's worktree branch into the default branch."""

    ticket_id: str


@dataclass(frozen=True)
class PublishCompleted:
    """Announce the ticket completed (the ``ticket_completed`` bus/emitter event)."""

    ticket_id: str


@dataclass(frozen=True)
class UnblockDependents:
    """Re-evaluate tickets blocked on ``ticket_id`` now that it resolved."""

    ticket_id: str


Action = SpawnAgent | MergeWorktree | PublishCompleted | UnblockDependents


# ---------------------------------------------------------------------------
# Transition table — (current status, event type) -> transition function.
# Each function is pure: (state, event) -> (next status, actions).
# ---------------------------------------------------------------------------

# Each handler accepts a narrower event type (TicketReady, AgentSucceeded, …);
# the table guarantees it only ever receives that type. ``Callable[...]`` keeps
# the readable narrow signatures assignable here (callable args are
# contravariant, so a fixed ``Event`` parameter would reject them).
_TransitionFn = Callable[..., tuple[EngineState, tuple[Action, ...]]]


def _on_ticket_ready(
    state: BuildState, event: TicketReady
) -> tuple[EngineState, tuple[Action, ...]]:
    return TicketStatus.IN_PROGRESS, (SpawnAgent(event.ticket_id, event.role),)


def _on_agent_succeeded(
    state: BuildState, event: AgentSucceeded
) -> tuple[EngineState, tuple[Action, ...]]:
    return BuildPhase.MERGING, (MergeWorktree(event.ticket_id),)


def _on_worktree_merged(
    state: BuildState, event: WorktreeMerged
) -> tuple[EngineState, tuple[Action, ...]]:
    return TicketStatus.RESOLVED, (
        PublishCompleted(event.ticket_id),
        UnblockDependents(event.ticket_id),
    )


def _on_agent_completed(
    state: BuildState, event: AgentCompleted
) -> tuple[EngineState, tuple[Action, ...]]:
    return event.status, ()


_TRANSITIONS: dict[tuple[EngineState, type[Event]], _TransitionFn] = {
    (TicketStatus.OPEN, TicketReady): _on_ticket_ready,
    (TicketStatus.IN_PROGRESS, AgentSucceeded): _on_agent_succeeded,
    (BuildPhase.MERGING, WorktreeMerged): _on_worktree_merged,
    (TicketStatus.IN_PROGRESS, AgentCompleted): _on_agent_completed,
}


def _with_status(state: BuildState, ticket_id: str, status: EngineState) -> BuildState:
    next_statuses = dict(state.statuses)
    next_statuses[ticket_id] = status
    return BuildState(statuses=next_statuses)


def decide(state: BuildState, event: Event) -> tuple[BuildState, tuple[Action, ...]]:
    """Pure transition: ``(state, event) -> (next_state, actions)``.

    Looks the transition up in ``_TRANSITIONS`` by the ticket's current status
    and the event type. An unknown ``(status, event)`` pair is a no-op (the
    state and an empty action tuple are returned unchanged). Synchronous and
    side-effect-free.
    """
    current = state.statuses.get(event.ticket_id)
    if current is None:
        _log.debug(
            "decide: unknown ticket_id %s — discarding %s",
            event.ticket_id,
            type(event).__name__,
        )
        return state, ()

    transition = _TRANSITIONS.get((current, type(event)))
    if transition is None:
        return state, ()

    next_status, actions = transition(state, event)
    return _with_status(state, event.ticket_id, next_status), actions
