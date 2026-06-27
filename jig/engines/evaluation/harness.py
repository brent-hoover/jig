"""The headless eval harness — composes the bones into a runnable pipeline.

``eval_run(build_config, fixtures) -> EvalResult`` drives the code-pipeline and
review loop headless: a ``FixtureRunAgent`` (Agent Runtime, Epic 3) behind the
Build engine's dispatch shell (Epic 4), with a ``Review`` (Enforcement, Epic 5)
over each fixture diff. No daemon, no TUI, no Orchestrator god-object, no MCP in
the path — that's the bones acceptance gate.

Bones drives the happy-path event sequence directly from the fake agent's
result. MVP wires the real feedback (dispatch emits the next event from the
agent run), labeled-diff corpora, and convergence metrics.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from jig.engines.build import (
    AgentCompleted,
    AgentSucceeded,
    BuildCoordinator,
    BuildState,
    Dispatcher,
    MergeWorktree,
    PublishCompleted,
    SpawnAgent,
    TicketReady,
    UnblockDependents,
    WorktreeMerged,
)
from jig.engines.enforcement import InvariantContext, MechanicalReview, Review
from jig.model import Finding
from jig.runtime import AgentRunResult, FixtureRunAgent
from jig.ticket import TicketStatus

# Non-success agent outcomes map onto the build engine's terminal statuses.
_NON_SUCCESS_STATUS: Mapping[str, TicketStatus] = {
    "failed": TicketStatus.FAILED,
    "blocked": TicketStatus.BLOCKED,
    "needs_info": TicketStatus.NEEDS_INFO,
}


@dataclass(frozen=True)
class BuildConfig:
    """What to drive through the pipeline: the tickets and the canned result the
    fixture agent returns for each."""

    tickets: tuple[str, ...]
    agent_result: AgentRunResult
    role: str = "dev"


@dataclass(frozen=True)
class Fixture:
    """A fixture for the review loop — a diff to enforce the invariants over."""

    name: str
    diff: str = ""


@dataclass(frozen=True)
class EvalResult:
    """The outcome of an eval run."""

    final_states: Mapping[str, str]
    findings: tuple[Finding, ...]
    agent_runs: int


async def eval_run(
    build_config: BuildConfig,
    fixtures: Sequence[Fixture],
    *,
    review: Review | None = None,
) -> EvalResult:
    """Run the code-pipeline + review loop headless on a fixture."""
    fixture_agent = FixtureRunAgent(build_config.agent_result)

    async def _spawn(action: object) -> None:
        await fixture_agent(action)  # the only thing actually "run"

    async def _noop(action: object) -> None:
        # Bones shims the real effects (merge/publish/unblock).
        return None

    dispatcher = Dispatcher(
        {
            SpawnAgent: _spawn,
            MergeWorktree: _noop,
            PublishCompleted: _noop,
            UnblockDependents: _noop,
        }
    )
    coordinator = BuildCoordinator(
        dispatcher,
        state=BuildState(statuses={t: TicketStatus.OPEN for t in build_config.tickets}),
    )

    # Drive each ticket through the pipeline, branching on the fake agent result.
    for ticket in build_config.tickets:
        await coordinator.handle(TicketReady(ticket_id=ticket, role=build_config.role))
        if build_config.agent_result.status == "success":
            await coordinator.handle(AgentSucceeded(ticket_id=ticket))
            await coordinator.handle(WorktreeMerged(ticket_id=ticket))
        else:
            terminal = _NON_SUCCESS_STATUS[build_config.agent_result.status]
            await coordinator.handle(AgentCompleted(ticket_id=ticket, status=terminal))

    # Review loop: enforce the invariants over each fixture diff.
    reviewer = review or MechanicalReview()
    findings: list[Finding] = []
    for fixture in fixtures:
        findings.extend(await reviewer(fixture.diff, InvariantContext()))

    final_states = {
        ticket: _status_value(coordinator.state, ticket)
        for ticket in build_config.tickets
    }
    return EvalResult(
        final_states=final_states,
        findings=tuple(findings),
        agent_runs=len(fixture_agent.calls),
    )


def _status_value(state: BuildState, ticket_id: str) -> str:
    return state.statuses[ticket_id].value
