"""Tests for the legacy-store AC backfill migration.

The migration walks ``.jig/store/tickets.jsonl``, replays each row to
reconstruct in-memory state, and appends an ``_op: update`` row for
every work-type ticket whose final description lacks an AC section.

These tests exercise the replay logic, the work-type filtering, the
AC-detection short-circuit, and the idempotency property (re-runs
are no-ops).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# Import the script's module under its slug. ``scripts/`` is on the
# path because pytest's rootdir picks it up; if that ever changes the
# tests will surface the breakage immediately.
import sys

_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import migrate_store_ticket_ac as migration  # noqa: E402

from jig.store.tickets import TicketStore  # noqa: E402


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r, sort_keys=True) for r in rows) + "\n")


def _row(
    op: str,
    ticket_id: str,
    **fields,
) -> dict:
    out: dict = {"_op": op, "_id": ticket_id}
    out.update(fields)
    return out


class TestReplay:
    def test_insert_then_update_yields_merged_state(self, tmp_path: Path) -> None:
        path = tmp_path / "t.jsonl"
        _write_jsonl(
            path,
            [
                _row(
                    "insert",
                    "t-1",
                    work_type="feature",
                    title="t",
                    description="",
                ),
                _row("update", "t-1", description="new"),
            ],
        )
        state = migration._replay(path)
        assert state["t-1"]["description"] == "new"

    def test_delete_removes_state(self, tmp_path: Path) -> None:
        path = tmp_path / "t.jsonl"
        _write_jsonl(
            path,
            [
                _row("insert", "t-1", work_type="feature", title="t"),
                _row("delete", "t-1"),
            ],
        )
        state = migration._replay(path)
        assert "t-1" not in state

    def test_update_against_unknown_id_is_skipped(self, tmp_path: Path) -> None:
        path = tmp_path / "t.jsonl"
        _write_jsonl(
            path,
            [_row("update", "ghost", description="x")],
        )
        state = migration._replay(path)
        assert state == {}


class TestWorkTypeFiltering:
    def test_system_types_are_not_backfilled(self, tmp_path: Path) -> None:
        path = tmp_path / "t.jsonl"
        _write_jsonl(
            path,
            [
                _row(
                    "insert",
                    "brief",
                    work_type="brief",
                    title="b",
                    description="",
                ),
            ],
        )
        state = migration._replay(path)
        todo = migration._tickets_needing_backfill(state)
        assert todo == []

    @pytest.mark.parametrize(
        "work_type",
        ["feature", "bugfix", "refactor", "spike", "perf", "migration"],
    )
    def test_work_types_with_missing_ac_are_backfilled(
        self, tmp_path: Path, work_type: str
    ) -> None:
        path = tmp_path / "t.jsonl"
        _write_jsonl(
            path,
            [
                _row(
                    "insert",
                    "t-1",
                    work_type=work_type,
                    title="t",
                    description="prose without AC",
                ),
            ],
        )
        state = migration._replay(path)
        todo = migration._tickets_needing_backfill(state)
        assert len(todo) == 1
        ticket_id, new_description = todo[0]
        assert ticket_id == "t-1"
        assert "## Acceptance criteria" in new_description
        assert "prose without AC" in new_description

    def test_work_type_with_existing_ac_is_skipped(self, tmp_path: Path) -> None:
        path = tmp_path / "t.jsonl"
        _write_jsonl(
            path,
            [
                _row(
                    "insert",
                    "t-1",
                    work_type="feature",
                    title="t",
                    description="## Acceptance criteria\n- already covered.\n",
                ),
            ],
        )
        state = migration._replay(path)
        todo = migration._tickets_needing_backfill(state)
        assert todo == []

    def test_legacy_type_field_is_translated(self, tmp_path: Path) -> None:
        """Pre-doc-03 records use ``type`` not ``work_type``. The
        migration must apply the legacy translation before evaluating
        AC requirement."""
        path = tmp_path / "t.jsonl"
        _write_jsonl(
            path,
            [
                _row(
                    "insert",
                    "t-1",
                    type="bug",  # legacy → bugfix → work type
                    title="t",
                    description="no AC here",
                ),
            ],
        )
        state = migration._replay(path)
        todo = migration._tickets_needing_backfill(state)
        assert len(todo) == 1


class TestEndToEnd:
    async def test_migrated_store_loads_through_ticket_store(
        self, tmp_path: Path
    ) -> None:
        """The migration's output JSONL must load cleanly through the
        production ``TicketStore`` (i.e. the validator no longer
        rejects the backfilled tickets)."""
        path = tmp_path / "tickets.jsonl"
        _write_jsonl(
            path,
            [
                _row(
                    "insert",
                    "t-1",
                    work_type="feature",
                    title="t",
                    description="legacy prose",
                    created_by="u",
                    created_at="2026-01-01T00:00:00+00:00",
                    updated_at="2026-01-01T00:00:00+00:00",
                ),
            ],
        )
        # Sanity: store reads fail BEFORE migration. The dict-level
        # load is lazy; validation fires on the first ``get()``.
        store = TicketStore(path)
        await store.load()
        with pytest.raises(Exception, match="Acceptance"):
            await store.get("t-1")

        # Migrate.
        changed = migration.migrate(path, dry_run=False)
        assert changed == 1

        # Store now loads cleanly.
        store2 = TicketStore(path)
        await store2.load()
        loaded = await store2.get("t-1")
        assert loaded is not None
        assert "## Acceptance criteria" in loaded.description
        assert "legacy prose" in loaded.description

    async def test_idempotent_rerun(self, tmp_path: Path) -> None:
        """Running the migration twice doesn't append a second update
        row (the first run already brought the ticket up to spec)."""
        path = tmp_path / "tickets.jsonl"
        _write_jsonl(
            path,
            [
                _row(
                    "insert",
                    "t-1",
                    work_type="feature",
                    title="t",
                    description="prose only",
                    created_by="u",
                    created_at="2026-01-01T00:00:00+00:00",
                    updated_at="2026-01-01T00:00:00+00:00",
                ),
            ],
        )
        first = migration.migrate(path, dry_run=False)
        assert first == 1
        second = migration.migrate(path, dry_run=False)
        assert second == 0


class TestDryRun:
    def test_dry_run_does_not_modify_file(self, tmp_path: Path) -> None:
        path = tmp_path / "t.jsonl"
        rows = [
            _row(
                "insert",
                "t-1",
                work_type="feature",
                title="t",
                description="prose",
            ),
        ]
        _write_jsonl(path, rows)
        original = path.read_text()
        changed = migration.migrate(path, dry_run=True)
        assert changed == 1
        assert path.read_text() == original
