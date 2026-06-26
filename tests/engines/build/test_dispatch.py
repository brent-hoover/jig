"""Build engine bones — the async dispatch shell (Epic 4, task 3).

``decide`` is pure and returns inert actions; ``Dispatcher`` is the only async
part — it executes each action via a registered effect handler. Handlers are
injected, so Bones shims them (and MVP wires the real spawn/merge/publish
effects). This is also where the Agent Runtime seam (Epic 3) plugs in: a
``SpawnAgent`` handler can drive a ``RunAgent``.
"""

from __future__ import annotations

import pytest

from jig.engines.build.decide import MergeWorktree, SpawnAgent
from jig.engines.build.dispatch import Dispatcher, UnhandledActionError
from jig.runtime import FixtureRunAgent


async def test_executes_each_action_via_its_handler() -> None:
    seen: list = []
    dispatcher = Dispatcher(
        {
            SpawnAgent: lambda a: _record(seen, a),
            MergeWorktree: lambda a: _record(seen, a),
        }
    )

    await dispatcher.execute(
        [SpawnAgent(ticket_id="jig-1", role="dev"), MergeWorktree(ticket_id="jig-1")]
    )

    assert seen == [
        SpawnAgent(ticket_id="jig-1", role="dev"),
        MergeWorktree(ticket_id="jig-1"),
    ]


async def test_unhandled_action_fails_loudly() -> None:
    dispatcher = Dispatcher({})
    with pytest.raises(UnhandledActionError):
        await dispatcher.execute([SpawnAgent(ticket_id="jig-1", role="dev")])


async def test_spawn_handler_can_drive_the_run_agent_seam() -> None:
    """The Build dispatch shell composes with the Agent Runtime seam: a
    SpawnAgent handler runs a RunAgent (here the fixture)."""
    run_agent = FixtureRunAgent()

    async def spawn(action: SpawnAgent) -> None:
        await run_agent(action)  # fixture ignores ctx; records the call

    dispatcher = Dispatcher({SpawnAgent: spawn})
    await dispatcher.execute([SpawnAgent(ticket_id="jig-1", role="dev")])

    assert run_agent.calls == [SpawnAgent(ticket_id="jig-1", role="dev")]


async def _record(seen: list, action: object) -> None:
    seen.append(action)
