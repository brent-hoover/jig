"""Backfill placeholder AC sections in legacy ticket stores.

The ``Ticket`` model now requires a discoverable Acceptance Criteria
section in the description for work-type tickets. Existing
``.jig/store/tickets.jsonl`` files predate that invariant — any
work-type ticket persisted before this validator landed will lack
the AC section and fail to load via ``TicketStore.load()`` /
``Ticket.model_validate()``.

This one-shot migration walks the JSONL and:

  1. Replays each row into an in-memory state map (same logic the
     ``Collection`` loader uses) to compute the final description for
     every ticket.
  2. For each work-type ticket whose final description lacks an AC
     section, appends an ``_op: update`` row that injects a
     placeholder AC. The original insert/update rows stay intact so
     the audit trail is preserved.

Idempotent: re-runs are no-ops because the appended update row makes
the validator-passing description durable. The script can be re-run
safely after partial failures.

Usage::

    uv run python scripts/migrate_store_ticket_ac.py \\
        /path/to/project/.jig/store/tickets.jsonl

Or, to dry-run (report what would change without writing)::

    uv run python scripts/migrate_store_ticket_ac.py \\
        --dry-run /path/to/project/.jig/store/tickets.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Imported lazily so the script remains runnable from any cwd as long
# as the package is installed (the migration is the only consumer of
# this script; tests import the helpers directly).
from jig.ticket import has_acceptance_criteria_section


# Work-type values whose tickets require an AC section. Mirrors
# ``jig.ticket._WORK_TYPES_REQUIRING_AC``; kept here as a string set
# rather than imported to avoid coupling the migration script to a
# private constant.
_WORK_TYPES_REQUIRING_AC: frozenset[str] = frozenset(
    {"feature", "bugfix", "refactor", "spike", "perf", "migration"}
)

# Legacy ``type`` values that need translation to a current
# ``work_type`` before we evaluate AC-requirement membership. Mirrors
# ``jig.ticket._LEGACY_TYPE_MIGRATION``.
_LEGACY_TYPE_MIGRATION: dict[str, str] = {
    "feature": "feature",
    "bug": "bugfix",
    "chore": "refactor",
    "task": "refactor",
    "question": "feature",
}

_PLACEHOLDER_AC_BLOCK = (
    "## Acceptance criteria\n"
    "- Backfilled by the AC migration; replace with the real AC.\n"
)


def _replay(jsonl_path: Path) -> dict[str, dict]:
    """Replay every row in ``jsonl_path`` into ``{ticket_id: state}``.

    Mirrors the ``Collection`` loader's semantics: ``_op="insert"`` /
    ``_op="upsert"`` writes the full record; ``_op="update"`` patches
    selected fields; ``_op="delete"`` removes the entry. The result is
    the same in-memory state the store would have at load time.
    """
    state: dict[str, dict] = {}
    for raw_line in jsonl_path.read_text().splitlines():
        line = raw_line.strip()
        if not line:
            continue
        record = json.loads(line)
        op = record.get("_op") or "insert"
        doc_id = record.get("_id")
        if doc_id is None:
            continue
        if op == "delete":
            state.pop(doc_id, None)
            continue
        if op in ("insert", "upsert"):
            state[doc_id] = {k: v for k, v in record.items() if k != "_op"}
            continue
        if op == "update":
            current = state.get(doc_id)
            if current is None:
                # Update for a non-existent ticket — leave it; the
                # store loader will raise its own error, which is the
                # correct surface.
                continue
            for key, value in record.items():
                if key in ("_op", "_id"):
                    continue
                current[key] = value
    return state


def _normalize_work_type(record: dict) -> str | None:
    """Return the effective ``work_type`` value for ``record``.

    Honours the legacy ``type`` migration so pre-doc-03 records (those
    still using the ``type`` key) are evaluated against the same
    AC-requirement set as current records.
    """
    work_type = record.get("work_type")
    if isinstance(work_type, str) and work_type:
        return work_type
    legacy = record.get("type")
    if isinstance(legacy, str) and legacy:
        return _LEGACY_TYPE_MIGRATION.get(legacy, legacy)
    return None


def _tickets_needing_backfill(state: dict[str, dict]) -> list[tuple[str, str]]:
    """Return ``(ticket_id, new_description)`` for every entry that
    needs an AC backfill."""
    todo: list[tuple[str, str]] = []
    for ticket_id, record in state.items():
        work_type = _normalize_work_type(record)
        if work_type is None or work_type not in _WORK_TYPES_REQUIRING_AC:
            continue
        description = record.get("description") or ""
        if has_acceptance_criteria_section(description):
            continue
        body = description.rstrip()
        if body:
            new_description = f"{body}\n\n{_PLACEHOLDER_AC_BLOCK}"
        else:
            new_description = _PLACEHOLDER_AC_BLOCK
        todo.append((ticket_id, new_description))
    return todo


def _append_update_rows(
    jsonl_path: Path,
    updates: list[tuple[str, str]],
) -> None:
    """Append one ``_op: update`` row per backfilled ticket."""
    if not updates:
        return
    now = datetime.now(timezone.utc).isoformat()
    rows = [
        json.dumps(
            {
                "_op": "update",
                "_id": ticket_id,
                "description": new_description,
                "updated_at": now,
            },
            sort_keys=True,
        )
        for ticket_id, new_description in updates
    ]
    with jsonl_path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(row + "\n")


def migrate(jsonl_path: Path, *, dry_run: bool) -> int:
    """Migrate one JSONL file in place. Returns the number of tickets
    that were backfilled (or would be, in dry-run mode)."""
    if not jsonl_path.is_file():
        print(f"warn: {jsonl_path} does not exist; skipping", file=sys.stderr)
        return 0
    state = _replay(jsonl_path)
    updates = _tickets_needing_backfill(state)
    if not updates:
        return 0
    for ticket_id, _ in updates:
        verb = "would backfill" if dry_run else "backfilling"
        print(f"{verb} AC for ticket {ticket_id}")
    if not dry_run:
        _append_update_rows(jsonl_path, updates)
    return len(updates)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "jsonl_path",
        type=Path,
        help="Path to .jig/store/tickets.jsonl",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing to the file.",
    )
    args = parser.parse_args(argv)
    changed = migrate(args.jsonl_path, dry_run=args.dry_run)
    suffix = " (dry run — no changes written)" if args.dry_run else ""
    print(f"\n{changed} ticket(s) backfilled{suffix}.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
