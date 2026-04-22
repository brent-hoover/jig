"""Scoped MCP server for agent-check verdict collection.

The check agent (implementation-aware or black-box) runs with a
deliberately tiny MCP surface: exactly one tool, ``check_verdict``,
that records its verdict + reasoning. Everything else the agent
needs lives in the initial prompt (for black-box) or via Read/Grep
against the worktree (for implementation-aware).

One call per run. Subsequent calls raise so a confused agent can't
flip its verdict mid-run and hide the first answer.

The runner owns the captured-verdict dict and passes it to the
factory so the tool handler can stash values without any shared
module-level state — multiple checks can run in parallel with
independent verdict slots.
"""

from __future__ import annotations

from typing import Literal, TypedDict

from claude_agent_sdk import create_sdk_mcp_server, tool


class CapturedVerdict(TypedDict, total=False):
    """Mutable dict the runner owns; the tool writes into it.

    ``verdict`` is ``None`` until the agent calls the tool. ``call_count``
    tracks duplicate calls so the runner can distinguish "agent never
    answered" (``call_count == 0``) from "agent answered but then tried
    to answer again" (``call_count > 1``).
    """

    verdict: Literal["pass", "fail"] | None
    reasoning: str
    call_count: int


def _fresh_slot() -> CapturedVerdict:
    return {"verdict": None, "reasoning": "", "call_count": 0}


def build_check_verdict_tool(captured: CapturedVerdict):
    """Build the ``check_verdict`` SDK tool bound to ``captured``.

    Exposed so tests can call the handler directly without mocking
    the full MCP server protocol; the runtime factory below composes
    it into a scoped server for agent use.
    """

    @tool(
        "check_verdict",
        "Record your verdict for this check. Call exactly once — "
        "a second call is an error. ``verdict`` must be 'pass' or "
        "'fail'; ``reasoning`` is the 1–3 sentence justification the "
        "evaluator will read.",
        {"verdict": str, "reasoning": str},
    )
    async def check_verdict(args):
        captured["call_count"] = captured.get("call_count", 0) + 1
        if captured["call_count"] > 1:
            raise RuntimeError(
                "check_verdict already called for this run; "
                "verdicts are immutable — post a thread note if you "
                "need to amend reasoning"
            )
        verdict = args.get("verdict")
        if verdict not in ("pass", "fail"):
            raise ValueError(f"verdict must be 'pass' or 'fail', got {verdict!r}")
        reasoning = args.get("reasoning") or ""
        captured["verdict"] = verdict  # type: ignore[typeddict-item]
        captured["reasoning"] = str(reasoning)
        return {"content": [{"type": "text", "text": "verdict recorded"}]}

    return check_verdict


def create_check_mcp_server(captured: CapturedVerdict):
    """Build a scoped MCP server with just ``check_verdict``.

    ``captured`` is a dict the runner reads after the agent quits.
    Initialize with ``_fresh_slot()`` or equivalent before passing —
    the tool won't reset any fields on its own.
    """

    check_verdict = build_check_verdict_tool(captured)
    return create_sdk_mcp_server(name="jig_check", tools=[check_verdict])


__all__ = [
    "CapturedVerdict",
    "_fresh_slot",
    "build_check_verdict_tool",
    "create_check_mcp_server",
]
