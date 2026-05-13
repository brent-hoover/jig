"""Single-turn ``claude-agent-sdk`` query wrapper for the eval harness.

Sends a prompt verbatim as a user message, with no tools and an empty system
primer, and captures the full message list plus tokens and cost. Everything
else (classification, code extraction, scoring) operates on the captured
transcript — the SDK call itself is the only network-touching step.

Note on temperature: ``claude-agent-sdk`` does not expose a per-call
temperature knob through its Claude Code transport in v1. The ``temperature``
parameter is recorded in the ``Cell`` identity for reproducibility metadata
but is not currently propagated to the underlying call. Cross-seed variance
comes from the model's own sampling, which is what we want for the
consistency axis anyway.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions, query
from claude_agent_sdk.types import ResultMessage


@dataclass
class InvocationResult:
    """Output of one single-turn agent SDK invocation."""

    transcript: list[dict[str, Any]]
    tokens: dict[str, int]
    cost_usd: float
    stop_reason: str | None


def _to_json_safe(value: Any) -> Any:
    """Recursively coerce SDK messages and blocks into JSON-safe structures.

    Dataclasses are recursed field-by-field rather than via ``asdict()`` so
    that *nested* dataclass instances (e.g. ``TextBlock`` inside an
    ``AssistantMessage.content`` list) also get a ``"type"`` tag. The
    classifier relies on those tags to identify text blocks.
    """
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, list | tuple):
        return [_to_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {k: _to_json_safe(v) for k, v in value.items()}
    if is_dataclass(value) and not isinstance(value, type):
        result: dict[str, Any] = {"type": type(value).__name__}
        for field in fields(value):
            result[field.name] = _to_json_safe(getattr(value, field.name))
        return result
    if hasattr(value, "__dict__"):
        return {"type": type(value).__name__, **_to_json_safe(vars(value))}
    return repr(value)


def serialize_message(message: Any) -> dict[str, Any]:
    """Public for tests; normalize one SDK message into a stored dict."""
    result = _to_json_safe(message)
    if not isinstance(result, dict):
        return {"type": type(message).__name__, "value": result}
    return result


async def invoke(
    prompt: str,
    *,
    model: str,
    temperature: float = 0.0,  # noqa: ARG001 — see module docstring
) -> InvocationResult:
    """Run one single-turn agent SDK query and return everything we'd want
    to persist about it.

    The transcript is the verbatim message stream as JSON-safe dicts. Token
    usage and cost are read from the trailing ``ResultMessage``; absent fields
    default to zero / ``None``.
    """
    options = ClaudeAgentOptions(
        model=model,
        max_turns=1,
        allowed_tools=[],
        system_prompt="",
        permission_mode="bypassPermissions",
    )
    transcript: list[dict[str, Any]] = []
    tokens: dict[str, int] = {}
    cost_usd = 0.0
    stop_reason: str | None = None

    async for message in query(prompt=prompt, options=options):
        transcript.append(serialize_message(message))
        if isinstance(message, ResultMessage):
            cost_usd = float(message.total_cost_usd or 0.0)
            stop_reason = message.stop_reason
            usage = message.usage or {}
            tokens = {
                key: int(value)
                for key, value in usage.items()
                if isinstance(value, int | float)
            }

    return InvocationResult(
        transcript=transcript,
        tokens=tokens,
        cost_usd=cost_usd,
        stop_reason=stop_reason,
    )
