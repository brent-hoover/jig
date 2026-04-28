"""Handler for {type: command, name: prompt_reply, args: {args: [prompt_id, reply]}}.

The TuiPromptHandler is awaiting a Future registered in PromptRegistry.
This handler resolves it.
"""
from typing import Any

from jig.tui.commands import register


@register("prompt_reply")
async def cmd_prompt_reply(
    *, args: list[str], orch, project_path, prompt_registry, **_kwargs
) -> dict[str, Any]:
    if len(args) < 2:
        return {"ok": False, "error": "prompt_reply expects [prompt_id, reply]"}
    prompt_id, reply = args[0], args[1]
    delivered = prompt_registry.deliver(prompt_id, reply)
    if not delivered:
        return {
            "ok": False,
            "error": f"no pending prompt with id {prompt_id!r} (already replied?)",
        }
    return {"ok": True, "data": {"prompt_id": prompt_id}}
