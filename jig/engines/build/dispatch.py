"""The async dispatch shell — executes ``decide()``'s inert actions.

``decide`` is pure and returns action dataclasses; ``Dispatcher`` is the only
async part of the Build engine. It maps each action type to a registered effect
handler (an async callable) and runs them in order. Handlers are injected, so
Bones shims them and MVP wires the real effects (spawn agent via the ``RunAgent``
seam, merge worktree, publish completion, unblock dependents).

An action with no registered handler fails loudly — a missing handler is a
wiring bug, not something to swallow.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Mapping

from jig.engines.build.decide import Action

EffectHandler = Callable[[Action], Awaitable[None]]


class UnhandledActionError(RuntimeError):
    """Raised when an action has no registered effect handler."""

    def __init__(self, action_type: type) -> None:
        super().__init__(f"no effect handler registered for {action_type.__name__}")
        self.action_type = action_type


class Dispatcher:
    """Executes inert actions via injected, type-keyed effect handlers.

    Delivery is **at-least-once**: a transition's actions run in order, but if a
    later action fails the coordinator doesn't commit, so a retry re-runs the
    whole transition's actions. Effect handlers must therefore be idempotent
    (and ``decide`` orders irreversible externally-visible actions last). Full
    per-action idempotency / ack tracking is MVP.
    """

    def __init__(self, handlers: Mapping[type, EffectHandler]) -> None:
        self._handlers = dict(handlers)

    async def execute(self, actions: Iterable[Action]) -> None:
        for action in actions:
            handler = self._handlers.get(type(action))
            if handler is None:
                raise UnhandledActionError(type(action))
            await handler(action)
