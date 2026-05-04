"""External-API recorded fixtures (Track E Final).

Per ``docs/dev-environment/design.md`` §"External-API recorded fixtures":
default ``replay_only`` mode + phase-gated ``record_new`` mode for
Shopify-style external dependencies. Final scope ships:

* :class:`FixtureMode` — ``REPLAY_ONLY`` (default), ``RECORD_NEW``
  (during spike work), ``BYPASS`` (operator escape hatch)
* :class:`FixtureCassette` — Pydantic model for one recorded
  request/response pair
* :class:`FixtureStore` — JSONL storage at
  ``.jig/dev/fixtures/<service_id>.jsonl``
* :class:`FixtureMiddleware` — wraps a duck-typed async HTTP client
  and honors the active mode
* :func:`request_signature` — stable hash for matching across record
  + replay
* :func:`fixture_mode_for_ticket` — phase-gating helper: SPIKE
  tickets default to ``record_new``; everything else defaults to
  ``replay_only``; per-spawn override wins

The middleware is httpx-shaped (``async request(method, url, ...)``)
but Final scope avoids adding httpx as a hard dep — any duck-typed
adapter suffices. Operators wire their concrete client (httpx /
aiohttp / etc.) at the call site.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from jig.atomic import atomic_write_text
from jig.ticket import Ticket, WorkType

__all__ = [
    "FIXTURE_MODE_ENV_VAR",
    "FIXTURES_DIR_RELATIVE",
    "AsyncHttpLike",
    "FixtureCassette",
    "FixtureMiddleware",
    "FixtureMissingError",
    "FixtureMode",
    "FixtureStore",
    "fixture_mode_for_ticket",
    "fixture_store_path",
    "fixtures_dir",
    "request_signature",
]

_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants + paths
# ---------------------------------------------------------------------------


FIXTURE_MODE_ENV_VAR = "JIG_FIXTURE_MODE"
FIXTURES_DIR_RELATIVE = Path(".jig") / "dev" / "fixtures"


def fixtures_dir(project_root: Path) -> Path:
    """``.jig/dev/fixtures/`` — one JSONL file per service id."""
    return project_root / FIXTURES_DIR_RELATIVE


def fixture_store_path(project_root: Path, service_id: str) -> Path:
    """``.jig/dev/fixtures/<service_id>.jsonl`` — JSONL cassette store for one service."""
    return fixtures_dir(project_root) / f"{service_id}.jsonl"


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------


class FixtureMode(str, Enum):
    """Active fixture mode for a given agent spawn.

    - ``REPLAY_ONLY`` (default): hits never escape; missing cassette
      raises ``FixtureMissingError``.
    - ``RECORD_NEW`` (spike phase, or override): missing cassettes are
      recorded against the real API; existing cassettes replay.
    - ``BYPASS`` (operator escape hatch): every request hits the real
      API directly; cassettes are ignored. Used when debugging the
      live integration.
    """

    REPLAY_ONLY = "replay_only"
    RECORD_NEW = "record_new"
    BYPASS = "bypass"


class FixtureMissingError(RuntimeError):
    """Raised when REPLAY_ONLY mode encounters a request without a cassette.

    The error surfaces enough context (service id, signature, request
    shape) for the operator to either record the cassette or fix the
    test setup. We deliberately do NOT silently fall back to the real
    API — that's the failure mode the design's ``replay_only`` default
    exists to prevent.
    """


# ---------------------------------------------------------------------------
# Cassette model
# ---------------------------------------------------------------------------


class FixtureCassette(BaseModel):
    """One recorded request/response pair persisted to the JSONL store."""

    model_config = ConfigDict(extra="forbid")

    service_id: str = Field(..., min_length=1)
    request_signature: str = Field(..., min_length=1)
    request: dict[str, Any] = Field(default_factory=dict)
    response: dict[str, Any] = Field(default_factory=dict)
    recorded_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    agent_id: str | None = None
    ticket_id: str | None = None


# ---------------------------------------------------------------------------
# Signature
# ---------------------------------------------------------------------------


def request_signature(
    method: str, url: str, body: dict[str, Any] | None
) -> str:
    """Stable SHA-256 of ``(method, url, body)`` — matches across record + replay.

    Body is serialized via JSON with sorted keys + separators so two
    semantically identical bodies produce the same signature regardless
    of dict iteration order. ``None`` is normalized to ``{}`` (the
    same hash an empty body produces).
    """
    body_json = json.dumps(
        body or {}, sort_keys=True, separators=(",", ":"), default=str
    )
    payload = f"{method.upper()}|{url}|{body_json}".encode()
    return hashlib.sha256(payload).hexdigest()


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class FixtureStore:
    """JSONL-backed cassette storage rooted at ``.jig/dev/fixtures/``.

    One file per service id; each line is a serialized
    :class:`FixtureCassette`. Append-only on record; lookups read the
    whole file (the cassette count per service stays small in practice).

    Atomic writes via :func:`jig.atomic.atomic_write_text`: we read the
    existing file, append the new line, and write the whole content
    atomically — same pattern :class:`OrphanLogEntry` uses.
    """

    def __init__(self, project_root: Path) -> None:
        self._root = project_root

    def _path(self, service_id: str) -> Path:
        return fixture_store_path(self._root, service_id)

    async def record(self, cassette: FixtureCassette) -> str:
        """Append ``cassette`` to the per-service JSONL file; return its signature."""
        path = self._path(cassette.service_id)
        existing = path.read_text() if path.is_file() else ""
        line = cassette.model_dump_json() + "\n"
        atomic_write_text(path, existing + line)
        return cassette.request_signature

    async def lookup(
        self, service_id: str, request_sig: str
    ) -> FixtureCassette | None:
        """Return the most recently-recorded cassette matching ``request_sig``."""
        rows = await self.list_for_service(service_id)
        match = next(
            (r for r in reversed(rows) if r.request_signature == request_sig),
            None,
        )
        return match

    async def list_for_service(
        self, service_id: str
    ) -> list[FixtureCassette]:
        """Return every cassette recorded for ``service_id``."""
        path = self._path(service_id)
        if not path.is_file():
            return []
        out: list[FixtureCassette] = []
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(FixtureCassette.model_validate_json(line))
            except Exception:
                _logger.warning(
                    "FixtureStore.list_for_service: skipping malformed "
                    "cassette in %s",
                    path,
                    exc_info=True,
                )
        return out

    async def list_services(self) -> list[str]:
        """Return every service id with at least one cassette on disk."""
        d = fixtures_dir(self._root)
        if not d.is_dir():
            return []
        return sorted(p.stem for p in d.glob("*.jsonl"))

    async def clear_service(self, service_id: str) -> None:
        """Delete the per-service JSONL file (operator-driven cleanup)."""
        path = self._path(service_id)
        if path.is_file():
            try:
                path.unlink()
            except OSError:
                _logger.warning(
                    "FixtureStore.clear_service failed for %s",
                    path,
                    exc_info=True,
                )


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class AsyncHttpLike(Protocol):
    """Duck-typed async HTTP client shape the middleware delegates to.

    The Protocol exists so callers can wire any backing client
    (httpx.AsyncClient, aiohttp.ClientSession, a thin adapter around
    ``urllib.request``...) without us pulling httpx as a hard dep.

    Final scope: returns a plain dict — the middleware doesn't try to
    model HTTP response objects from the various libraries' shapes.
    Callers wrap their client to produce ``{"status": int, ...}``.
    """

    async def request(  # pragma: no cover — Protocol body
        self,
        method: str,
        url: str,
        *,
        headers: dict | None = None,
        body: dict | None = None,
    ) -> dict[str, Any]:
        ...


class FixtureMiddleware:
    """Wrap an async HTTP client with cassette record/replay semantics.

    Per design.md §"External-API recorded fixtures":

    - ``REPLAY_ONLY``: ``record_or_replay`` returns the cassette body
      or raises :class:`FixtureMissingError`. Never calls the client.
    - ``RECORD_NEW``: returns the cassette body if one exists; otherwise
      calls the client, records the result, returns it.
    - ``BYPASS``: always calls the client; ignores cassettes.

    Construction takes a ``client`` (the duck-typed backing HTTP) and a
    ``mode``. Tests pass a fake client; production wires the real one.
    """

    def __init__(
        self,
        *,
        service_id: str,
        store: FixtureStore,
        mode: FixtureMode,
        client: AsyncHttpLike | None,
        agent_id: str | None = None,
        ticket_id: str | None = None,
    ) -> None:
        self._service_id = service_id
        self._store = store
        self._mode = mode
        self._client = client
        self._agent_id = agent_id
        self._ticket_id = ticket_id

    @property
    def mode(self) -> FixtureMode:
        return self._mode

    async def record_or_replay(
        self,
        method: str,
        url: str,
        headers: dict | None,
        body: dict | None,
    ) -> dict[str, Any]:
        """The single hot-path entry point.

        Returns the response dict per the active mode. See class
        docstring for per-mode semantics.
        """
        sig = request_signature(method, url, body)
        if self._mode == FixtureMode.BYPASS:
            return await self._call_client(method, url, headers, body)
        cassette = await self._store.lookup(self._service_id, sig)
        if self._mode == FixtureMode.REPLAY_ONLY:
            if cassette is None:
                raise FixtureMissingError(
                    f"no cassette for service={self._service_id!r} "
                    f"method={method!r} url={url!r} (mode=replay_only); "
                    "record one with mode=record_new or fix the test setup"
                )
            return dict(cassette.response)
        if self._mode == FixtureMode.RECORD_NEW:
            if cassette is not None:
                return dict(cassette.response)
            response = await self._call_client(method, url, headers, body)
            await self._store.record(
                FixtureCassette(
                    service_id=self._service_id,
                    request_signature=sig,
                    request={
                        "method": method,
                        "url": url,
                        "headers": headers or {},
                        "body": body or {},
                    },
                    response=response,
                    agent_id=self._agent_id,
                    ticket_id=self._ticket_id,
                )
            )
            return response
        # Defensive: enums add new variants over time.
        raise RuntimeError(f"unknown FixtureMode {self._mode!r}")

    async def _call_client(
        self,
        method: str,
        url: str,
        headers: dict | None,
        body: dict | None,
    ) -> dict[str, Any]:
        if self._client is None:
            raise RuntimeError(
                f"FixtureMiddleware mode={self._mode.value} requires a "
                "backing client; pass one to the constructor"
            )
        return await self._client.request(
            method, url, headers=headers, body=body
        )


# ---------------------------------------------------------------------------
# Phase-gating
# ---------------------------------------------------------------------------


def fixture_mode_for_ticket(
    ticket: Ticket, *, override: str | None = None
) -> FixtureMode:
    """Pick the right fixture mode for an agent spawning to work this ticket.

    Per design.md §"External-API recorded fixtures": SPIKE tickets
    default to ``record_new`` (the spike's job is to grow the fixture
    corpus against the real API); everything else defaults to
    ``replay_only``. Per-spawn ``override`` (e.g. an
    ``arch_propose_spike`` carrying ``fixture_mode_override="bypass"``)
    wins over the work-type default.

    Raises ``ValueError`` for an override that isn't a valid
    :class:`FixtureMode` value — the call site (typically
    ``orchestrator_hook``) wants to fail loud rather than silently
    fall back to the default.
    """
    if override is not None:
        try:
            return FixtureMode(override)
        except ValueError as exc:
            raise ValueError(
                f"fixture_mode override {override!r} is not a valid "
                f"FixtureMode (got {[m.value for m in FixtureMode]})"
            ) from exc
    if ticket.work_type == WorkType.SPIKE:
        return FixtureMode.RECORD_NEW
    return FixtureMode.REPLAY_ONLY
