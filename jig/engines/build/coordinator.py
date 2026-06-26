"""BuildCoordinator — the thin coordinator that composes the Build engine.

Wires the three pieces together: the pure ``decide`` core, the async
``Dispatcher`` shell, and the ``Supervisor``. ``handle(event)`` runs one step —
decide the transition, advance state, dispatch the actions. ``supervise(...)``
turns detector signals into supervisory events (which never mutate state).

Bones proves the loop on the happy path with shimmed effect handlers. MVP
subscribes this to the bus, routes the orchestrator's ticket transitions through
it, and feeds supervisory events back into ``decide`` — at which point the
existing ``orchestrator.py`` becomes a thin facade over this coordinator. Bones
leaves ``orchestrator.py`` untouched.
"""

from __future__ import annotations

from jig.engines.build.decide import BuildState, Event, decide
from jig.engines.build.dispatch import Dispatcher
from jig.engines.build.supervisor import Supervisor, SupervisoryEvent


class BuildCoordinator:
    """Composes ``decide`` + ``Dispatcher`` + ``Supervisor`` over a build state."""

    def __init__(
        self,
        dispatcher: Dispatcher,
        *,
        supervisor: Supervisor | None = None,
        state: BuildState | None = None,
    ) -> None:
        self._dispatcher = dispatcher
        self._supervisor = supervisor or Supervisor()
        self._state = state or BuildState(statuses={})

    @property
    def state(self) -> BuildState:
        return self._state

    async def handle(self, event: Event) -> None:
        """One step: decide the transition, advance state, dispatch the actions."""
        self._state, actions = decide(self._state, event)
        await self._dispatcher.execute(actions)

    def supervise(self, **signals) -> list[SupervisoryEvent]:
        """Translate detector signals into supervisory events (no mutation)."""
        return self._supervisor.observe(**signals)
