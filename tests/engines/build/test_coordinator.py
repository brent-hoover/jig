"""Build engine bones — the thin BuildCoordinator (Epic 4, task 5).

Composes the pieces: pure ``decide`` + async ``Dispatcher`` + ``Supervisor``.
Feed it an event → it decides → advances state → dispatches the actions. Bones
proves the loop end-to-end on the happy path; MVP wires it to the bus and routes
the orchestrator's transitions through it (the existing ``orchestrator.py`` is
untouched in Bones).
"""

from __future__ import annotations

from jig.engines.build.coordinator import BuildCoordinator
from jig.engines.build.decide import (
    AgentSucceeded,
    BuildState,
    MergeWorktree,
    PublishCompleted,
    SpawnAgent,
    TicketReady,
    UnblockDependents,
    WorktreeMerged,
)
from jig.engines.build.dispatch import Dispatcher
from jig.engines.build.supervisor import DeadlockNudgeNeeded
from jig.runtime import FixtureRunAgent
from jig.ticket import TicketStatus


def _recording_dispatcher(sink: list) -> Dispatcher:
    async def record(action: object) -> None:
        sink.append(action)

    return Dispatcher(
        {
            SpawnAgent: record,
            MergeWorktree: record,
            PublishCompleted: record,
            UnblockDependents: record,
        }
    )


async def test_happy_path_drives_open_to_resolved() -> None:
    actions: list = []
    coordinator = BuildCoordinator(
        _recording_dispatcher(actions),
        state=BuildState(statuses={"jig-1": TicketStatus.OPEN}),
    )

    await coordinator.handle(TicketReady(ticket_id="jig-1", role="dev"))
    await coordinator.handle(AgentSucceeded(ticket_id="jig-1"))
    await coordinator.handle(WorktreeMerged(ticket_id="jig-1"))

    assert coordinator.state.statuses["jig-1"] == TicketStatus.RESOLVED
    assert actions == [
        SpawnAgent(ticket_id="jig-1", role="dev"),
        MergeWorktree(ticket_id="jig-1"),
        PublishCompleted(ticket_id="jig-1"),
        UnblockDependents(ticket_id="jig-1"),
    ]


async def test_spawn_flows_through_the_run_agent_seam() -> None:
    run_agent = FixtureRunAgent()

    async def spawn(action: SpawnAgent) -> None:
        await run_agent(action)

    coordinator = BuildCoordinator(
        Dispatcher({SpawnAgent: spawn}),
        state=BuildState(statuses={"jig-1": TicketStatus.OPEN}),
    )

    await coordinator.handle(TicketReady(ticket_id="jig-1", role="dev"))

    assert run_agent.calls == [SpawnAgent(ticket_id="jig-1", role="dev")]


def test_supervise_emits_events_without_touching_state() -> None:
    coordinator = BuildCoordinator(
        Dispatcher({}),
        state=BuildState(statuses={"jig-1": TicketStatus.OPEN}),
    )

    events = coordinator.supervise(nudged=["entry-1"])

    assert events == [DeadlockNudgeNeeded(entry_id="entry-1")]
    assert coordinator.state.statuses["jig-1"] == TicketStatus.OPEN
