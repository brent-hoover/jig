"""Reference solution for ``todo_cli``.

Sanity-checks that the hidden tests are passable. Never shown to the
candidate or the judge. Kept idiomatic and minimal — a tighter solution is
fine, but this is the floor we expect a competent prompt to clear.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_DATA_FILE = Path("todo.json")


def _load() -> list[dict[str, object]]:
    if not _DATA_FILE.exists():
        return []
    return json.loads(_DATA_FILE.read_text(encoding="utf-8"))


def _save(items: list[dict[str, object]]) -> None:
    _DATA_FILE.write_text(json.dumps(items), encoding="utf-8")


def _cmd_add(args: list[str]) -> int:
    if not args:
        print("usage: add <text>", file=sys.stderr)
        return 2
    items = _load()
    items.append({"text": " ".join(args), "done": False})
    _save(items)
    return 0


def _cmd_list(args: list[str]) -> int:
    if args:
        print("usage: list", file=sys.stderr)
        return 2
    for n, item in enumerate(_load(), start=1):
        prefix = "[x] " if item.get("done") else ""
        print(f"{n}. {prefix}{item['text']}")
    return 0


def _cmd_done(args: list[str]) -> int:
    if len(args) != 1:
        print("usage: done <n>", file=sys.stderr)
        return 2
    try:
        index = int(args[0])
    except ValueError:
        print(f"not an integer: {args[0]}", file=sys.stderr)
        return 2
    items = _load()
    if not 1 <= index <= len(items):
        print(f"out of range: {index}", file=sys.stderr)
        return 2
    items[index - 1]["done"] = True
    _save(items)
    return 0


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: todo.py <add|list|done> [...]", file=sys.stderr)
        return 2
    command, rest = argv[0], argv[1:]
    handlers = {"add": _cmd_add, "list": _cmd_list, "done": _cmd_done}
    handler = handlers.get(command)
    if handler is None:
        print(f"unknown command: {command}", file=sys.stderr)
        return 2
    return handler(rest)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
