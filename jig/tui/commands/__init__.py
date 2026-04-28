"""Slash command handlers — daemon-side implementations.

Each command is an async callable that receives args + access to the
orchestrator's stores and returns ``{"ok": bool, "data": Any}`` or
``{"ok": False, "error": str}``. The same handler is used by the TUI
input dispatcher and by --print mode.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any


CommandHandler = Callable[..., Awaitable[dict[str, Any]]]


_REGISTRY: dict[str, CommandHandler] = {}


def register(name: str):
    def deco(fn: CommandHandler) -> CommandHandler:
        _REGISTRY[name] = fn
        return fn
    return deco


def get_handler(name: str) -> CommandHandler | None:
    return _REGISTRY.get(name)


def known_commands() -> list[str]:
    return sorted(_REGISTRY)


# Import command modules so their @register decorators run at import time.
from jig.tui.commands import status  # noqa: F401, E402
