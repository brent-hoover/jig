#!/usr/bin/env python3
"""PreToolUse hook: auto-approve safe git Bash commands.

Reads a hook event from stdin. If the command is a chain of safe git
verbs (optionally prefixed with `cd <path> &&`), prints an approve
decision; otherwise prints an empty object so normal permission rules
apply.

Destructive or networked git ops (push, reset --hard, rebase, clean -f,
force-push, history rewrites) are deliberately not allow-listed — they
fall through to the regular prompt.

Chaining with `;` or `|` is rejected; only `&&` is permitted so a
failure in an earlier segment aborts the chain.
"""

from __future__ import annotations

import json
import re
import sys

SAFE_VERBS = (
    "status",
    "diff",
    "log",
    "show",
    "branch",
    "rev-parse",
    "ls-files",
    "add",
    "commit",
    "restore",
    "stash",
    "checkout",
    "switch",
    "fetch",
    "tag",
    "config",  # read-only — writes are gated because we forbid --global below
)

# Args that must NEVER appear unprompted, even with a safe verb.
FORBIDDEN_FLAGS = (
    "--force",
    "-f",
    "--hard",
    "--global",
    "--system",
    "--no-verify",
    "--no-gpg-sign",
)


def is_safe_segment(seg: str) -> bool:
    seg = seg.strip()
    if not seg.startswith("git "):
        # Allow a single `cd <path>` prefix segment.
        return bool(re.fullmatch(r"cd [^;&|]+", seg))
    tokens = seg.split()
    if len(tokens) < 2:
        return False
    verb = tokens[1]
    if verb not in SAFE_VERBS:
        return False
    for tok in tokens[2:]:
        if tok in FORBIDDEN_FLAGS:
            return False
    return True


def main() -> None:
    try:
        event = json.load(sys.stdin)
    except json.JSONDecodeError:
        print("{}")
        return
    cmd = event.get("tool_input", {}).get("command", "")
    if not cmd or ";" in cmd or "|" in cmd:
        print("{}")
        return
    segments = [s.strip() for s in cmd.split("&&")]
    if not segments or not all(is_safe_segment(s) for s in segments):
        print("{}")
        return
    print(json.dumps({"decision": "approve"}))


if __name__ == "__main__":
    main()
