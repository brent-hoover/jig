"""Append-only JSONL store of ``RunRecord`` rows.

One record per line. Reads are tolerant of trailing newlines but fail loudly
on any line that doesn't round-trip through ``RunRecord.model_validate_json``
— a malformed line implies a bug, not a recoverable runtime condition.

Concurrency model for v1: a single async runner process, possibly with many
in-flight coroutines. The ``asyncio.Lock`` serializes appends within that
process; cross-process locking is deferred until we need a second writer.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from jig.evals.prompt_style_eval.models import Cell, RunRecord


class Store:
    """Append-only JSONL store of ``RunRecord`` rows."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = asyncio.Lock()

    async def append(self, record: RunRecord) -> None:
        """Append one record. Serialized within the process via asyncio.Lock."""
        line = record.model_dump_json()
        async with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")

    def read_all(self) -> Iterator[RunRecord]:
        """Yield every record in the store in file order."""
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as f:
            for line_num, raw in enumerate(f, start=1):
                stripped = raw.strip()
                if not stripped:
                    continue
                try:
                    yield RunRecord.model_validate_json(stripped)
                except Exception as exc:
                    raise ValueError(
                        f"{self.path}:{line_num}: malformed record: {exc}"
                    ) from exc

    def count_matching(self, cell: Cell) -> int:
        """Count records whose Cell matches ``cell`` exactly."""
        return sum(1 for record in self.read_all() if record.cell == cell)

    def query(self, **filters: Any) -> Iterator[RunRecord]:
        """Yield records matching every keyword filter.

        Keys may be either ``RunRecord`` fields (e.g. ``outcome``,
        ``derived_from``) or ``Cell`` fields (e.g. ``task_id``,
        ``prompt_id``). Cell fields take precedence — they tend to be the
        ones callers actually filter on.
        """
        cell_fields = set(Cell.model_fields)
        record_fields = set(RunRecord.model_fields)
        unknown = set(filters) - cell_fields - record_fields
        if unknown:
            raise KeyError(f"unknown filter field(s): {sorted(unknown)}")
        for record in self.read_all():
            if all(self._matches(record, key, value) for key, value in filters.items()):
                yield record

    @staticmethod
    def _matches(record: RunRecord, key: str, value: Any) -> bool:
        if key in Cell.model_fields:
            return getattr(record.cell, key) == value
        return getattr(record, key) == value
