"""CheckResultsStore — append-JSONL storage for verification results.

Mirrors ``CheckpointStore`` — Collection-backed, indexed on
``ticket_id`` / ``phase`` / ``check_name`` so the gating layer
(Phase 5 Task D) can ask "what did the latest run of check X for
phase Y on ticket Z say?" cheaply.

Records are never updated in place: a re-run is a new append. The
"latest" queries sort by ``created_at`` and take the tail. That
keeps the audit trail intact — evaluators and humans can see every
attempt, not just the most recent one.
"""

from __future__ import annotations

from pathlib import Path

from jig.check_results import CheckResult
from jig.store.collection import Collection


class CheckResultsStore:
    def __init__(self, path: Path) -> None:
        self._collection = Collection(
            path,
            index_fields=["ticket_id", "phase", "check_name"],
        )

    async def load(self) -> None:
        await self._collection.load()

    # ---- writes ---------------------------------------------------------

    async def post(self, result: CheckResult) -> str:
        raw = result.model_dump(mode="json", by_alias=True)
        return await self._collection.insert(raw)

    # ---- reads ----------------------------------------------------------

    def _load(self, raw: dict) -> CheckResult:
        return CheckResult.model_validate(raw)

    async def get(self, result_id: str) -> CheckResult | None:
        raw = await self._collection.get(result_id)
        return None if raw is None else self._load(raw)

    async def for_ticket(self, ticket_id: str) -> list[CheckResult]:
        raws = await self._collection.find_where(ticket_id=ticket_id)
        results = [self._load(r) for r in raws]
        results.sort(key=lambda r: r.created_at)
        return results

    async def for_phase(
        self, ticket_id: str, phase: str
    ) -> list[CheckResult]:
        raws = await self._collection.find_where(
            ticket_id=ticket_id, phase=phase
        )
        results = [self._load(r) for r in raws]
        results.sort(key=lambda r: r.created_at)
        return results

    async def latest_for_check(
        self, ticket_id: str, phase: str, check_name: str
    ) -> CheckResult | None:
        """Most recent result for a specific check in a phase."""
        raws = await self._collection.find_where(
            ticket_id=ticket_id, phase=phase, check_name=check_name
        )
        if not raws:
            return None
        results = [self._load(r) for r in raws]
        results.sort(key=lambda r: r.created_at)
        return results[-1]

    async def latest_batch(
        self, ticket_id: str, phase: str
    ) -> list[CheckResult]:
        """Latest run of each declared check in a phase.

        When the same check fires multiple times (re-run on rejected
        handoff, retries), only the most recent per check_name is
        surfaced. Used by the gating layer to decide whether the
        current state of the worktree passes.
        """
        all_results = await self.for_phase(ticket_id, phase)
        latest: dict[str, CheckResult] = {}
        for r in all_results:
            latest[r.check_name] = r  # sorted by created_at; last wins
        return list(latest.values())


__all__ = [
    "CheckResultsStore",
]
