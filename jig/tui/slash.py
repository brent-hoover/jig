"""Parse and dispatch slash commands.

Slash commands are the fast path for known operations. Free-text
inputs (anything not starting with `/`) go to the concierge agent in
Phase 3.
"""
from __future__ import annotations

import shlex
from dataclasses import dataclass


@dataclass
class ParsedSlash:
    name: str
    args: list[str]


class SlashParseError(ValueError):
    """Raised when a slash command is malformed."""


def parse_slash(line: str) -> ParsedSlash:
    """Parse `/name arg1 arg2 ...` into a ParsedSlash.

    Quoting follows shell rules (shlex). Raises SlashParseError if the
    line doesn't start with `/` or is empty after the slash.
    """
    line = line.strip()
    if not line.startswith("/"):
        raise SlashParseError(f"not a slash command: {line!r}")
    body = line[1:].strip()
    if not body:
        raise SlashParseError("empty slash command")
    try:
        parts = shlex.split(body)
    except ValueError as exc:
        raise SlashParseError(f"malformed quoting: {exc}") from exc
    return ParsedSlash(name=parts[0], args=parts[1:])
