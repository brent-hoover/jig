"""Persistence for analytics events.

Append-only JSONL through the existing ``Collection`` primitive,
mirroring the ``ThreadStore`` shape but loading rows back through
the ``AnalyticsEvent`` discriminated union via ``parse_event``.

Loading the full stream into memory is acceptable in v1 because
event volume is bounded by project scope. Once corpora grow
across many projects (or once we ship the consumer half from
``docs/analytics/problem.md``), expect a streaming read API to
land alongside this.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from jig.analytics.events import AnalyticsEvent, parse_event
from jig.store.collection import Collection

_logger = logging.getLogger(__name__)


class AnalyticsStore:
    """Typed analytics event store.

    Wraps ``Collection`` directly rather than ``TypedCollection``
    because the latter is fixed to a single concrete model and
    can't validate a discriminated union. Same trade-off as
    ``ThreadStore``.
    """

    def __init__(self, path: Path) -> None:
        # Index by event kind so re-plan-trigger queries
        # ("how many escalations since revision N?") and
        # consumer queries ("all OperatorOverride events") stay
        # O(matches) rather than O(stream).
        self._collection = Collection(path, index_fields=["kind"])

    async def load(self) -> None:
        await self._collection.load()

    # ---- writes ----------------------------------------------------------

    async def append(self, event: AnalyticsEvent) -> str:
        """Append an event. Returns the event's id.

        ``event`` must already be a typed ``AnalyticsEvent``
        subclass instance; raw-dict callers should go through
        ``parse_event`` first to get schema validation.
        """
        # ``model_dump(by_alias=True)`` so the ``id`` round-trips as
        # ``_id`` for the underlying collection's id convention.
        raw = event.model_dump(mode="json", by_alias=True)
        return await self._collection.insert(raw)

    # ---- reads -----------------------------------------------------------

    def _load(self, raw: dict[str, Any]) -> AnalyticsEvent:
        return parse_event(raw)

    async def all(self) -> list[AnalyticsEvent]:
        """Load every event. v1 only — large corpora will need streaming."""
        raws = await self._collection.find()
        return [self._load(r) for r in raws]

    async def by_kind(self, kind: str) -> list[AnalyticsEvent]:
        """All events of a given kind. Index-backed."""
        raws = await self._collection.find_where(kind=kind)
        return [self._load(r) for r in raws]

    async def by_correlation(self, correlation_id: str) -> list[AnalyticsEvent]:
        """All events sharing a correlation_id, ordered by timestamp.

        Not index-backed — scans the full stream. Acceptable in v1;
        if correlation queries become hot, add ``correlation_id`` to
        ``index_fields`` above.
        """
        raws = await self._collection.find()
        events = [self._load(r) for r in raws if r.get("correlation_id") == correlation_id]
        events.sort(key=lambda e: e.timestamp)
        return events
