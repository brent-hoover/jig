"""Per-orchestrator-session cache for URI resolution.

Cache keys: ``(authority, path-tuple, revision)``.  Values: the resolved
``ResolvedUri``.  Fragment is *not* part of the key — the resolver loads the
artifact (which is what we cache) and applies the fragment slice on top, so
two URIs that differ only in fragment share the cached load.

Invalidation is event-driven (the design's primary path) with a TTL fallback
that bounds worst-case staleness when an event subscription is missed.

Wired up at orchestrator startup via ``subscribe_to_events`` so the cache
sees every contract amendment / wireframe revision / ticket state change
without each call site remembering to invalidate by hand.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from jig.uri.parser import Authority, ProjectUri
from jig.uri.resolver import ResolvedUri

# Tuple key shape: (authority, path-tuple, revision-or-None).
_Key = tuple[str, tuple[str, ...], int | None]


@dataclass(frozen=True)
class _Entry:
    value: ResolvedUri
    expires_at: float


class UriResolverCache:
    """LRU-less per-session cache for resolved URIs.

    No bound on size: a single orchestrator session has bounded artifact
    count (modules + capabilities + tickets) and TTL eviction trims what
    isn't reused.  When the cache becomes a memory concern we'll add a
    cap; not now.
    """

    DEFAULT_TTL_SECONDS = 300.0  # 5 minutes; matches design.md mitigation note.

    def __init__(
        self,
        *,
        ttl_seconds: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.ttl_seconds = (
            self.DEFAULT_TTL_SECONDS if ttl_seconds is None else ttl_seconds
        )
        self._clock = clock
        self._entries: dict[_Key, _Entry] = {}

    # ---- core get/put ---------------------------------------------------

    def _key(self, parsed: ProjectUri) -> _Key:
        # Fragment is intentionally excluded — see module docstring.
        return (parsed.authority, parsed.path, parsed.revision)

    def get(self, parsed: ProjectUri) -> ResolvedUri | None:
        """Return the cached value or ``None`` (miss or expired)."""
        key = self._key(parsed)
        entry = self._entries.get(key)
        if entry is None:
            return None
        if self._clock() > entry.expires_at:
            # TTL fallback — drop on read so subscribers needn't sweep.
            self._entries.pop(key, None)
            return None
        return entry.value

    def put(self, parsed: ProjectUri, value: ResolvedUri) -> None:
        """Store ``value`` under the parsed URI's key."""
        self._entries[self._key(parsed)] = _Entry(
            value=value,
            expires_at=self._clock() + self.ttl_seconds,
        )

    # ---- invalidation ---------------------------------------------------

    def invalidate(
        self,
        authority: Authority | str,
        path_prefix: tuple[str, ...] | None = None,
    ) -> None:
        """Drop entries for ``authority`` (and optionally a path prefix).

        ``path_prefix=None`` drops every entry under ``authority``.  When
        a prefix is given, only entries whose path *starts with* that
        prefix are dropped — sibling artifacts in the same authority
        survive.
        """
        to_drop: list[_Key] = []
        for key in self._entries:
            ent_authority, ent_path, _ = key
            if ent_authority != authority:
                continue
            if path_prefix is not None and ent_path[: len(path_prefix)] != path_prefix:
                continue
            to_drop.append(key)
        for key in to_drop:
            self._entries.pop(key, None)

    def clear(self) -> None:
        """Wipe the cache.  Useful at session shutdown / test teardown."""
        self._entries.clear()

    # ---- event subscription --------------------------------------------

    def subscribe_to_events(self, emitter: object) -> None:
        """Wire invalidation to the analytics emitter.

        We monkey-patch the emitter's ``emit`` and ``emit_nowait`` methods
        to invoke ``handle_event`` first.  The emitter doesn't expose a
        listener API today (it's a one-way write path) so this is the
        minimum-leakage hook — emitter behaviour is otherwise unchanged.

        Idempotent: subscribing the same cache to the same emitter twice
        does not double-fire (we tag the wrapped methods).
        """
        # Avoid double-wrapping — important for tests that share a cache.
        if getattr(emitter, "_uri_cache_wired", False):
            return
        original_emit = emitter.emit  # type: ignore[attr-defined]
        original_emit_nowait = emitter.emit_nowait  # type: ignore[attr-defined]

        async def emit(event):  # type: ignore[no-untyped-def]
            self.handle_event(event)
            return await original_emit(event)

        def emit_nowait(event):  # type: ignore[no-untyped-def]
            self.handle_event(event)
            original_emit_nowait(event)

        emitter.emit = emit  # type: ignore[attr-defined]
        emitter.emit_nowait = emit_nowait  # type: ignore[attr-defined]
        emitter._uri_cache_wired = True  # type: ignore[attr-defined]

    def handle_event(self, event: object) -> None:
        """Apply the invalidation rules for one analytics event.

        Public so tests can drive invalidation without an emitter.
        """
        # Late import to avoid analytics → uri import cycles at module load.
        from jig.analytics.events import (
            ContractAmended,
            TicketStateChanged,
            WireframeRevised,
        )

        if isinstance(event, ContractAmended):
            module_id = _module_id_from_contract_uri(event.contract_uri)
            if module_id is not None:
                self.invalidate(
                    "arch", path_prefix=("modules", module_id)
                )
            # The architecture document references modules; safest to drop
            # all architecture-rooted entries when any contract changes.
            self.invalidate("arch", path_prefix=("architecture",))
            return
        if isinstance(event, WireframeRevised):
            self.invalidate(
                "design", path_prefix=("wireframes", event.screen_id)
            )
            return
        if isinstance(event, TicketStateChanged):
            self.invalidate(
                "store", path_prefix=("tickets", event.ticket_id)
            )
            return


def _module_id_from_contract_uri(contract_uri: str) -> str | None:
    """Pull ``<module>`` out of ``project://arch/modules/<module>/contracts...``.

    Returns ``None`` for shared / non-module contract URIs — those callers
    fall back to the architecture-level invalidate which always fires.
    """
    from jig.uri.parser import parse_project_uri

    try:
        parsed = parse_project_uri(contract_uri)
    except Exception:
        return None
    if parsed.authority != "arch":
        return None
    if len(parsed.path) >= 2 and parsed.path[0] == "modules":
        return parsed.path[1]
    return None
