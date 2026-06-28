"""Tests for the URI resolver cache + event-driven invalidation.

Track A Final scope. Cache is per-orchestrator-session: keyed by
``(authority, path-tuple, revision)``, invalidated by events from the
orchestrator's analytics emitter (ContractAmended → arch entries,
WireframeRevised → design entries, TicketStateChanged → store entries).

TTL fallback bounds worst-case staleness if invalidation events are
missed. Cache is opt-in via the ``cache=`` kwarg on
``resolve_project_uri``; bare callers are unaffected.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from jig.analytics.emitter import EventEmitter
from jig.analytics.events import (
    ContractAmended,
    TicketStateChanged,
    WireframeRevised,
)
from jig.analytics.store import AnalyticsStore
from jig.spec_schema import (
    Behavior,
    Capability,
    CapabilityState,
    StructuredSpec,
)
from jig.uri import ResolvedUri, parse_project_uri
from jig.uri.cache import UriResolverCache


def _ts() -> datetime:
    return datetime(2026, 4, 27, tzinfo=timezone.utc)


def _resolved(kind: str = "spec", data: object | None = None) -> ResolvedUri:
    return ResolvedUri(
        kind=kind,
        data=data if data is not None else {"name": "x"},
        source_path=None,
        revision=None,
    )


# ---------------------------------------------------------------------------
# Hit / miss semantics
# ---------------------------------------------------------------------------


def test_get_returns_none_on_miss():
    cache = UriResolverCache()
    assert cache.get(parse_project_uri("project://spec")) is None


def test_put_then_get_returns_stored_value():
    cache = UriResolverCache()
    parsed = parse_project_uri("project://spec/capabilities/x")
    value = _resolved(kind="capability", data={"id": "x"})
    cache.put(parsed, value)
    assert cache.get(parsed) is value


def test_distinct_revisions_are_separate_entries():
    cache = UriResolverCache()
    a = parse_project_uri("project://arch/architecture@revision:1")
    b = parse_project_uri("project://arch/architecture@revision:2")
    av = _resolved(kind="arch", data={"r": 1})
    bv = _resolved(kind="arch", data={"r": 2})
    cache.put(a, av)
    cache.put(b, bv)
    assert cache.get(a) is av
    assert cache.get(b) is bv


def test_fragment_is_not_part_of_cache_key():
    """Cache stores the loaded artifact; fragments slice already-loaded data."""
    cache = UriResolverCache()
    a = parse_project_uri("project://arch/architecture#a")
    b = parse_project_uri("project://arch/architecture#b")
    value = _resolved(kind="arch")
    cache.put(a, value)
    assert cache.get(b) is value


# ---------------------------------------------------------------------------
# TTL fallback
# ---------------------------------------------------------------------------


def test_ttl_expiration_drops_entry(monkeypatch: pytest.MonkeyPatch):
    fake_now = [1000.0]

    def now() -> float:
        return fake_now[0]

    cache = UriResolverCache(ttl_seconds=60.0, clock=now)
    parsed = parse_project_uri("project://spec")
    cache.put(parsed, _resolved())
    fake_now[0] += 30
    assert cache.get(parsed) is not None
    fake_now[0] += 31
    # 30 + 31 = 61 > ttl=60, so expired.
    assert cache.get(parsed) is None


def test_default_ttl_is_five_minutes():
    cache = UriResolverCache()
    assert cache.ttl_seconds == pytest.approx(300.0)


# ---------------------------------------------------------------------------
# Invalidation by authority + path prefix
# ---------------------------------------------------------------------------


def test_invalidate_authority_drops_only_that_authority():
    cache = UriResolverCache()
    spec = parse_project_uri("project://spec/capabilities/x")
    arch = parse_project_uri("project://arch/architecture")
    cache.put(spec, _resolved())
    cache.put(arch, _resolved(kind="arch"))
    cache.invalidate("arch")
    assert cache.get(spec) is not None
    assert cache.get(arch) is None


def test_invalidate_with_path_prefix_only_drops_matching():
    cache = UriResolverCache()
    contracts = parse_project_uri("project://arch/modules/catalog-ingest/contracts")
    architecture = parse_project_uri("project://arch/architecture")
    other_module = parse_project_uri("project://arch/modules/categorization/contracts")
    cache.put(contracts, _resolved(kind="arch"))
    cache.put(architecture, _resolved(kind="arch"))
    cache.put(other_module, _resolved(kind="arch"))

    cache.invalidate("arch", path_prefix=("modules", "catalog-ingest"))

    assert cache.get(contracts) is None
    assert cache.get(architecture) is not None
    assert cache.get(other_module) is not None


def test_invalidate_path_prefix_matches_exact_root():
    cache = UriResolverCache()
    arch_root = parse_project_uri("project://arch/architecture")
    cache.put(arch_root, _resolved(kind="arch"))
    cache.invalidate("arch", path_prefix=("architecture",))
    assert cache.get(arch_root) is None


# ---------------------------------------------------------------------------
# Event subscription wiring
# ---------------------------------------------------------------------------


@pytest.fixture
async def emitter(tmp_path):
    store = AnalyticsStore(tmp_path / "events.jsonl")
    await store.load()
    return EventEmitter(store)


async def test_subscribe_invalidates_on_contract_amended(emitter):
    cache = UriResolverCache()
    contracts = parse_project_uri("project://arch/modules/catalog-ingest/contracts")
    architecture = parse_project_uri("project://arch/architecture")
    other_module = parse_project_uri("project://arch/modules/categorization/contracts")
    cache.put(contracts, _resolved(kind="arch"))
    cache.put(architecture, _resolved(kind="arch"))
    cache.put(other_module, _resolved(kind="arch"))

    cache.subscribe_to_events(emitter)

    await emitter.emit(
        ContractAmended(
            contract_uri=(
                "project://arch/modules/catalog-ingest/contracts#owns/products"
            ),
            from_revision=1,
            to_revision=2,
            source="sa_initial_pass",
            operator_confirmed=True,
            breaking_change=False,
            timestamp=_ts(),
        )
    )

    # Both this module's contracts AND architecture invalidate; sibling stays.
    assert cache.get(contracts) is None
    assert cache.get(architecture) is None
    assert cache.get(other_module) is not None


async def test_subscribe_invalidates_on_wireframe_revised(emitter):
    cache = UriResolverCache()
    target = parse_project_uri("project://design/wireframes/post-a-job")
    sibling = parse_project_uri("project://design/wireframes/onboarding")
    cache.put(target, _resolved(kind="design"))
    cache.put(sibling, _resolved(kind="design"))

    cache.subscribe_to_events(emitter)

    await emitter.emit(
        WireframeRevised(
            screen_id="post-a-job",
            from_revision=1,
            to_revision=2,
            trigger="operator_feedback",
            timestamp=_ts(),
        )
    )

    assert cache.get(target) is None
    assert cache.get(sibling) is not None


async def test_subscribe_invalidates_on_ticket_state_changed(emitter):
    cache = UriResolverCache()
    target = parse_project_uri("project://store/tickets/t-001")
    sibling = parse_project_uri("project://store/tickets/t-002")
    cache.put(target, _resolved(kind="store"))
    cache.put(sibling, _resolved(kind="store"))

    cache.subscribe_to_events(emitter)

    await emitter.emit(
        TicketStateChanged(
            ticket_id="t-001",
            from_state="open",
            to_state="in_progress",
            timestamp=_ts(),
        )
    )

    assert cache.get(target) is None
    assert cache.get(sibling) is not None


async def test_unrelated_event_does_not_invalidate(emitter):
    cache = UriResolverCache()
    arch = parse_project_uri("project://arch/architecture")
    cache.put(arch, _resolved(kind="arch"))
    cache.subscribe_to_events(emitter)

    # TicketStateChanged shouldn't touch arch entries.
    await emitter.emit(
        TicketStateChanged(
            ticket_id="t-001",
            from_state="open",
            to_state="done",
            timestamp=_ts(),
        )
    )
    assert cache.get(arch) is not None


# ---------------------------------------------------------------------------
# resolve_project_uri integration — opt-in cache kwarg
# ---------------------------------------------------------------------------


def _spec() -> StructuredSpec:
    return StructuredSpec(
        name="todo",
        summary="x",
        capabilities=[
            Capability(
                id="due-dates",
                title="Due dates",
                state=CapabilityState.PLANNED,
                behaviors=[
                    Behavior(id="set", description="x", acceptance_criteria=["a"]),
                ],
                created_at=_ts(),
                last_updated=_ts(),
                state_changed_at=_ts(),
            ),
        ],
        generated_at=_ts(),
    )


def test_resolve_project_uri_uses_cache_when_provided(monkeypatch, tmp_path):
    """Second resolve with a cache hits the stored entry, skips loader."""
    from jig.uri import resolver as resolver_mod

    cache = UriResolverCache()
    spec = _spec()
    call_count = [0]

    def fake_loader(_root):
        call_count[0] += 1
        return spec, tmp_path / "spec.yaml"

    monkeypatch.setattr("jig.spec_loader.load_structured_spec", fake_loader)
    out1 = resolver_mod.resolve_project_uri(
        "project://spec/capabilities/due-dates", tmp_path, cache=cache
    )
    out2 = resolver_mod.resolve_project_uri(
        "project://spec/capabilities/due-dates", tmp_path, cache=cache
    )
    assert out1 is out2
    assert call_count[0] == 1


def test_resolve_project_uri_no_cache_kwarg_is_backward_compat(monkeypatch, tmp_path):
    """Default behaviour (no cache kwarg) calls loader every time."""
    from jig.uri import resolver as resolver_mod

    spec = _spec()
    call_count = [0]

    def fake_loader(_root):
        call_count[0] += 1
        return spec, tmp_path / "spec.yaml"

    monkeypatch.setattr("jig.spec_loader.load_structured_spec", fake_loader)
    resolver_mod.resolve_project_uri("project://spec/capabilities/due-dates", tmp_path)
    resolver_mod.resolve_project_uri("project://spec/capabilities/due-dates", tmp_path)
    assert call_count[0] == 2
