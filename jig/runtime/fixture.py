"""FixtureRunAgent — a canned ``RunAgent`` for headless evals (Epic 3, task 4).

Returns a preset ``AgentRunResult`` without spawning anything, and records the
contexts it was called with so tests/evals can assert on dispatch. This is the
bone the headless harness (Epic 8) drives in place of the real agent stack.
"""

from __future__ import annotations

from jig.runtime.contract import AgentRunResult


class FixtureRunAgent:
    """A ``RunAgent`` that returns a fixed result.

    Deliberately context-agnostic — it ignores ``ctx`` entirely — so the
    parameter and the recorded ``calls`` are typed ``object``, matching what
    callers (and the eval harness) actually pass.
    """

    def __init__(self, result: AgentRunResult | None = None) -> None:
        self._result = result or AgentRunResult(status="success", final_text="")
        self.calls: list[object] = []

    async def __call__(self, ctx: object) -> AgentRunResult:
        self.calls.append(ctx)
        return self._result
