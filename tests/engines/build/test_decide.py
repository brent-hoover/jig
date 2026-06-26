"""Spike 1 (#201) — is ``decide()`` genuinely pure under the async SDK?

The question: actions include "spawn agent", which is inherently async. Can
``decide(state, event) -> (next_state, actions)`` stay a pure, synchronous
function? These tests are the proof: ``decide`` is sync, deterministic, and
mutates nothing; the spawn is an inert ``SpawnAgent`` dataclass that an async
shell executes afterward. Purity holds — the seam is functional-core /
imperative-shell.
"""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import is_dataclass

import pytest

from jig.engines.build.decide import (
    AgentCompleted,
    AgentSucceeded,
    BuildPhase,
    BuildState,
    MergeWorktree,
    PublishCompleted,
    SpawnAgent,
    TicketReady,
    UnblockDependents,
    WorktreeMerged,
    decide,
)
from jig.ticket import TicketStatus


def test_decide_is_a_plain_sync_function() -> None:
    # The crux: decide does NOT need to await to decide.
    assert not inspect.iscoroutinefunction(decide)


def test_ready_ticket_transitions_to_in_progress_and_spawns() -> None:
    state = BuildState(statuses={"jig-1": TicketStatus.OPEN})

    next_state, actions = decide(state, TicketReady(ticket_id="jig-1", role="dev"))

    assert next_state.statuses["jig-1"] == TicketStatus.IN_PROGRESS
    assert actions == (SpawnAgent(ticket_id="jig-1", role="dev"),)


def test_the_spawn_action_is_an_inert_dataclass_not_a_coroutine() -> None:
    _, actions = decide(
        BuildState(statuses={"jig-1": TicketStatus.OPEN}),
        TicketReady(ticket_id="jig-1", role="dev"),
    )
    (action,) = actions
    assert is_dataclass(action)
    assert not inspect.iscoroutine(action)


def test_decide_does_not_mutate_the_input_state() -> None:
    state = BuildState(statuses={"jig-1": TicketStatus.OPEN})

    decide(state, TicketReady(ticket_id="jig-1", role="dev"))

    # Original state is untouched — decide returns a new state.
    assert state.statuses["jig-1"] == TicketStatus.OPEN


def test_build_state_is_isolated_from_caller_mutation() -> None:
    # Mutating the mapping the caller passed must not change the snapshot —
    # otherwise the purity/determinism guarantee leaks.
    statuses = {"jig-1": TicketStatus.OPEN}
    state = BuildState(statuses=statuses)

    statuses["jig-1"] = TicketStatus.FAILED

    assert state.statuses["jig-1"] == TicketStatus.OPEN


def test_build_state_snapshot_rejects_mutation() -> None:
    state = BuildState(statuses={"jig-1": TicketStatus.OPEN})
    with pytest.raises(TypeError):
        state.statuses["jig-1"] = TicketStatus.FAILED  # type: ignore[index]


def test_decide_is_deterministic() -> None:
    state = BuildState(statuses={"jig-1": TicketStatus.OPEN})
    event = TicketReady(ticket_id="jig-1", role="dev")

    assert decide(state, event) == decide(state, event)


def test_non_ready_ticket_is_a_no_op() -> None:
    state = BuildState(statuses={"jig-1": TicketStatus.IN_PROGRESS})

    next_state, actions = decide(state, TicketReady(ticket_id="jig-1", role="dev"))

    assert actions == ()
    assert next_state == state


def test_agent_completion_sets_a_non_success_terminal_status() -> None:
    # AgentCompleted carries explicit non-success terminals (failed/blocked/…).
    state = BuildState(statuses={"jig-1": TicketStatus.IN_PROGRESS})

    next_state, actions = decide(
        state, AgentCompleted(ticket_id="jig-1", status=TicketStatus.FAILED)
    )

    assert next_state.statuses["jig-1"] == TicketStatus.FAILED
    assert actions == ()


def test_agent_completed_cannot_shortcut_to_resolved() -> None:
    # RESOLVED must go through the merge path; AgentCompleted(RESOLVED) no-ops
    # rather than skipping merge/publish/unblock.
    state = BuildState(statuses={"jig-1": TicketStatus.IN_PROGRESS})

    next_state, actions = decide(
        state, AgentCompleted(ticket_id="jig-1", status=TicketStatus.RESOLVED)
    )

    assert next_state.statuses["jig-1"] == TicketStatus.IN_PROGRESS
    assert actions == ()


def test_agent_success_enters_merging_and_triggers_a_merge() -> None:
    state = BuildState(statuses={"jig-1": TicketStatus.IN_PROGRESS})

    next_state, actions = decide(state, AgentSucceeded(ticket_id="jig-1"))

    assert next_state.statuses["jig-1"] == BuildPhase.MERGING
    assert actions == (MergeWorktree(ticket_id="jig-1"),)


def test_duplicate_agent_success_does_not_merge_twice() -> None:
    state = BuildState(statuses={"jig-1": TicketStatus.IN_PROGRESS})

    state, first = decide(state, AgentSucceeded(ticket_id="jig-1"))
    state, second = decide(state, AgentSucceeded(ticket_id="jig-1"))

    assert first == (MergeWorktree(ticket_id="jig-1"),)
    assert second == ()  # already merging — no second merge
    assert state.statuses["jig-1"] == BuildPhase.MERGING


def test_worktree_merged_without_a_pending_merge_is_a_no_op() -> None:
    # WorktreeMerged is only valid from MERGING; a stray one must not resolve.
    state = BuildState(statuses={"jig-1": TicketStatus.IN_PROGRESS})

    next_state, actions = decide(state, WorktreeMerged(ticket_id="jig-1"))

    assert actions == ()
    assert next_state.statuses["jig-1"] == TicketStatus.IN_PROGRESS


def test_merge_resolves_and_unblocks_dependents() -> None:
    state = BuildState(statuses={"jig-1": BuildPhase.MERGING})

    next_state, actions = decide(state, WorktreeMerged(ticket_id="jig-1"))

    assert next_state.statuses["jig-1"] == TicketStatus.RESOLVED
    assert actions == (
        UnblockDependents(ticket_id="jig-1"),
        PublishCompleted(ticket_id="jig-1"),
    )


def test_full_happy_path_open_to_resolved() -> None:
    state = BuildState(statuses={"jig-1": TicketStatus.OPEN})
    collected: list = []

    for event in (
        TicketReady(ticket_id="jig-1", role="dev"),
        AgentSucceeded(ticket_id="jig-1"),
        WorktreeMerged(ticket_id="jig-1"),
    ):
        state, actions = decide(state, event)
        collected.extend(actions)

    assert state.statuses["jig-1"] == TicketStatus.RESOLVED
    assert collected == [
        SpawnAgent(ticket_id="jig-1", role="dev"),
        MergeWorktree(ticket_id="jig-1"),
        UnblockDependents(ticket_id="jig-1"),
        PublishCompleted(ticket_id="jig-1"),
    ]


def test_event_with_no_table_entry_is_a_no_op() -> None:
    # WorktreeMerged on an OPEN ticket has no transition — no-op.
    state = BuildState(statuses={"jig-1": TicketStatus.OPEN})

    next_state, actions = decide(state, WorktreeMerged(ticket_id="jig-1"))

    assert actions == ()
    assert next_state == state


def test_event_for_unknown_ticket_id_is_a_no_op() -> None:
    # A ticket absent from state (vs. present-but-no-transition) is also a no-op.
    state = BuildState(statuses={})

    next_state, actions = decide(state, TicketReady(ticket_id="jig-99", role="dev"))

    assert actions == ()
    assert next_state == state


async def test_async_shell_executes_the_inert_actions() -> None:
    """The async part lives in the shell, never in decide."""
    spawned: list[tuple[str, str]] = []

    async def execute(action: SpawnAgent) -> None:
        await asyncio.sleep(0)  # the inherently-async work happens here
        spawned.append((action.ticket_id, action.role))

    state = BuildState(statuses={"jig-1": TicketStatus.OPEN})
    next_state, actions = decide(state, TicketReady(ticket_id="jig-1", role="dev"))

    for action in actions:
        await execute(action)

    assert spawned == [("jig-1", "dev")]
    assert next_state.statuses["jig-1"] == TicketStatus.IN_PROGRESS
