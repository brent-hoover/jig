"""Tests for vcr-style external-API fixture recording (Track E Final).

Covers the FixtureStore / FixtureMiddleware / signature contract per
``docs/dev-environment/design.md`` §"External-API recorded fixtures":

* round-trip record + replay produces identical bodies
* signatures are stable across calls and discriminate on (method, url,
  body)
* REPLAY_ONLY raises a clear error on missing cassette (no silent
  real-API fallback)
* RECORD_NEW falls through to a backing client when no cassette exists
* BYPASS skips the cassette layer entirely
* ``fixture_mode_for_ticket`` gates SPIKE work_type → record_new and
  honors per-spawn override
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from jig.dev_env.fixtures import (
    FixtureCassette,
    FixtureMiddleware,
    FixtureMissingError,
    FixtureMode,
    FixtureStore,
    fixture_mode_for_ticket,
    fixture_store_path,
    request_signature,
)
from jig.ticket import Ticket, WorkType


# ---------------------------------------------------------------------------
# Signature
# ---------------------------------------------------------------------------


def test_request_signature_is_stable_across_calls() -> None:
    s1 = request_signature("GET", "https://api.example.com/x", {"k": "v"})
    s2 = request_signature("GET", "https://api.example.com/x", {"k": "v"})
    assert s1 == s2


def test_request_signature_distinguishes_method() -> None:
    s_get = request_signature("GET", "https://api.example.com/x", None)
    s_post = request_signature("POST", "https://api.example.com/x", None)
    assert s_get != s_post


def test_request_signature_distinguishes_url() -> None:
    a = request_signature("GET", "https://api.example.com/a", None)
    b = request_signature("GET", "https://api.example.com/b", None)
    assert a != b


def test_request_signature_distinguishes_body() -> None:
    a = request_signature("POST", "https://api.example.com/x", {"a": 1})
    b = request_signature("POST", "https://api.example.com/x", {"b": 2})
    assert a != b


def test_request_signature_handles_none_body() -> None:
    """A body of ``None`` matches an empty dict's signature shape — both stable."""
    s = request_signature("GET", "https://api.example.com/x", None)
    assert isinstance(s, str)
    assert len(s) > 0


# ---------------------------------------------------------------------------
# FixtureStore
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fixture_store_record_and_lookup_round_trip(
    tmp_path: Path,
) -> None:
    store = FixtureStore(tmp_path)
    sig = request_signature("GET", "https://api.shopify.com/products", None)
    cassette = FixtureCassette(
        service_id="shopify-api",
        request_signature=sig,
        request={"method": "GET", "url": "https://api.shopify.com/products"},
        response={"status": 200, "json": {"products": []}},
    )
    await store.record(cassette)
    found = await store.lookup("shopify-api", sig)
    assert found is not None
    assert found.response == cassette.response


@pytest.mark.asyncio
async def test_fixture_store_lookup_missing_returns_none(tmp_path: Path) -> None:
    store = FixtureStore(tmp_path)
    found = await store.lookup("shopify-api", "no-such-sig")
    assert found is None


@pytest.mark.asyncio
async def test_fixture_store_writes_per_service_jsonl(tmp_path: Path) -> None:
    store = FixtureStore(tmp_path)
    sig = request_signature("GET", "https://api.example.com/x", None)
    await store.record(
        FixtureCassette(
            service_id="shopify-api",
            request_signature=sig,
            request={"method": "GET"},
            response={"status": 200},
        )
    )
    target = fixture_store_path(tmp_path, "shopify-api")
    assert target.is_file()
    body = [json.loads(line) for line in target.read_text().splitlines()]
    assert body[0]["service_id"] == "shopify-api"


@pytest.mark.asyncio
async def test_fixture_store_list_returns_all_for_service(tmp_path: Path) -> None:
    store = FixtureStore(tmp_path)
    for path in ("/a", "/b", "/c"):
        sig = request_signature("GET", f"https://api.example.com{path}", None)
        await store.record(
            FixtureCassette(
                service_id="shopify-api",
                request_signature=sig,
                request={"method": "GET", "url": path},
                response={"status": 200},
            )
        )
    rows = await store.list_for_service("shopify-api")
    assert len(rows) == 3
    assert {r.request.get("url") for r in rows} == {"/a", "/b", "/c"}


@pytest.mark.asyncio
async def test_fixture_store_clear_service_removes_file(tmp_path: Path) -> None:
    store = FixtureStore(tmp_path)
    sig = request_signature("GET", "https://x", None)
    await store.record(
        FixtureCassette(
            service_id="shopify-api",
            request_signature=sig,
            request={"method": "GET"},
            response={"status": 200},
        )
    )
    assert fixture_store_path(tmp_path, "shopify-api").is_file()
    await store.clear_service("shopify-api")
    assert not fixture_store_path(tmp_path, "shopify-api").is_file()


# ---------------------------------------------------------------------------
# FixtureMiddleware modes
# ---------------------------------------------------------------------------


class _FakeClient:
    """Duck-typed async HTTP client — minimum surface the middleware needs."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.next_response: dict = {"status": 200, "body": "real"}

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict | None = None,
        body: dict | None = None,
    ) -> dict:
        self.calls.append(
            {"method": method, "url": url, "headers": headers, "body": body}
        )
        return dict(self.next_response)


@pytest.mark.asyncio
async def test_replay_only_returns_recorded_response(tmp_path: Path) -> None:
    store = FixtureStore(tmp_path)
    sig = request_signature("GET", "https://api.shopify.com/products", None)
    await store.record(
        FixtureCassette(
            service_id="shopify-api",
            request_signature=sig,
            request={"method": "GET"},
            response={"status": 200, "body": "from-fixture"},
        )
    )
    client = _FakeClient()
    mw = FixtureMiddleware(
        service_id="shopify-api",
        store=store,
        mode=FixtureMode.REPLAY_ONLY,
        client=client,
    )
    resp = await mw.record_or_replay(
        "GET", "https://api.shopify.com/products", None, None
    )
    assert resp == {"status": 200, "body": "from-fixture"}
    # The real client must NOT be called.
    assert client.calls == []


@pytest.mark.asyncio
async def test_replay_only_missing_cassette_raises(tmp_path: Path) -> None:
    store = FixtureStore(tmp_path)
    client = _FakeClient()
    mw = FixtureMiddleware(
        service_id="shopify-api",
        store=store,
        mode=FixtureMode.REPLAY_ONLY,
        client=client,
    )
    with pytest.raises(FixtureMissingError) as exc:
        await mw.record_or_replay(
            "GET", "https://api.shopify.com/products", None, None
        )
    # Operator-readable error: surface the URL + the mode.
    assert "shopify-api" in str(exc.value)
    assert "replay_only" in str(exc.value).lower()
    assert client.calls == []  # Must NOT silently fall back to real API.


@pytest.mark.asyncio
async def test_record_new_records_then_returns_real_response(
    tmp_path: Path,
) -> None:
    store = FixtureStore(tmp_path)
    client = _FakeClient()
    client.next_response = {"status": 200, "body": "fresh"}
    mw = FixtureMiddleware(
        service_id="shopify-api",
        store=store,
        mode=FixtureMode.RECORD_NEW,
        client=client,
    )
    resp = await mw.record_or_replay(
        "GET", "https://api.shopify.com/orders", None, None
    )
    assert resp == {"status": 200, "body": "fresh"}
    assert len(client.calls) == 1
    # The recording landed in the store.
    sig = request_signature("GET", "https://api.shopify.com/orders", None)
    found = await store.lookup("shopify-api", sig)
    assert found is not None
    assert found.response == {"status": 200, "body": "fresh"}


@pytest.mark.asyncio
async def test_record_new_replays_existing_cassette(tmp_path: Path) -> None:
    """RECORD_NEW only records when there's no existing cassette."""
    store = FixtureStore(tmp_path)
    sig = request_signature("GET", "https://api.shopify.com/x", None)
    await store.record(
        FixtureCassette(
            service_id="shopify-api",
            request_signature=sig,
            request={"method": "GET"},
            response={"status": 200, "body": "from-fixture"},
        )
    )
    client = _FakeClient()
    client.next_response = {"status": 200, "body": "real"}
    mw = FixtureMiddleware(
        service_id="shopify-api",
        store=store,
        mode=FixtureMode.RECORD_NEW,
        client=client,
    )
    resp = await mw.record_or_replay(
        "GET", "https://api.shopify.com/x", None, None
    )
    assert resp == {"status": 200, "body": "from-fixture"}
    assert client.calls == []


@pytest.mark.asyncio
async def test_bypass_calls_real_client_directly(tmp_path: Path) -> None:
    store = FixtureStore(tmp_path)
    sig = request_signature("GET", "https://api.shopify.com/x", None)
    # Even when a cassette exists, BYPASS skips it and goes to real API.
    await store.record(
        FixtureCassette(
            service_id="shopify-api",
            request_signature=sig,
            request={"method": "GET"},
            response={"status": 200, "body": "from-fixture"},
        )
    )
    client = _FakeClient()
    client.next_response = {"status": 201, "body": "real"}
    mw = FixtureMiddleware(
        service_id="shopify-api",
        store=store,
        mode=FixtureMode.BYPASS,
        client=client,
    )
    resp = await mw.record_or_replay(
        "GET", "https://api.shopify.com/x", None, None
    )
    assert resp == {"status": 201, "body": "real"}
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_record_new_no_client_raises(tmp_path: Path) -> None:
    """RECORD_NEW with no backing client + no cassette must fail loud."""
    store = FixtureStore(tmp_path)
    mw = FixtureMiddleware(
        service_id="shopify-api",
        store=store,
        mode=FixtureMode.RECORD_NEW,
        client=None,
    )
    with pytest.raises(RuntimeError):
        await mw.record_or_replay(
            "GET", "https://api.shopify.com/x", None, None
        )


# ---------------------------------------------------------------------------
# Phase-gating: fixture_mode_for_ticket
# ---------------------------------------------------------------------------


def test_fixture_mode_for_spike_ticket_is_record_new() -> None:
    t = Ticket(
        id="t-1",
        work_type=WorkType.SPIKE,
        title="x",
        created_by="u",
    )
    assert fixture_mode_for_ticket(t) == FixtureMode.RECORD_NEW


def test_fixture_mode_for_feature_ticket_defaults_to_replay_only() -> None:
    t = Ticket(
        id="t-1",
        work_type=WorkType.FEATURE,
        title="x",
        created_by="u",
    )
    assert fixture_mode_for_ticket(t) == FixtureMode.REPLAY_ONLY


def test_fixture_mode_for_ticket_honors_override() -> None:
    """Operator override beats the work_type-based default."""
    t = Ticket(
        id="t-1",
        work_type=WorkType.FEATURE,
        title="x",
        created_by="u",
    )
    assert (
        fixture_mode_for_ticket(t, override="bypass") == FixtureMode.BYPASS
    )
    assert (
        fixture_mode_for_ticket(t, override="record_new")
        == FixtureMode.RECORD_NEW
    )


def test_fixture_mode_for_ticket_invalid_override_raises() -> None:
    t = Ticket(
        id="t-1",
        work_type=WorkType.SPIKE,
        title="x",
        created_by="u",
    )
    with pytest.raises(ValueError):
        fixture_mode_for_ticket(t, override="not-a-mode")


# ---------------------------------------------------------------------------
# orchestrator_hook.build_fixture_env — env var injection
# ---------------------------------------------------------------------------


def test_build_fixture_env_spike_ticket_records() -> None:
    from jig.dev_env.orchestrator_hook import build_fixture_env

    t = Ticket(
        id="t-1",
        work_type=WorkType.SPIKE,
        title="x",
        created_by="u",
    )
    env = build_fixture_env(t)
    assert env == {"JIG_FIXTURE_MODE": "record_new"}


def test_build_fixture_env_feature_ticket_replays() -> None:
    from jig.dev_env.orchestrator_hook import build_fixture_env

    t = Ticket(
        id="t-1",
        work_type=WorkType.FEATURE,
        title="x",
        created_by="u",
    )
    env = build_fixture_env(t)
    assert env == {"JIG_FIXTURE_MODE": "replay_only"}


def test_build_fixture_env_override_wins() -> None:
    from jig.dev_env.orchestrator_hook import build_fixture_env

    t = Ticket(
        id="t-1",
        work_type=WorkType.FEATURE,
        title="x",
        created_by="u",
    )
    env = build_fixture_env(t, override="bypass")
    assert env == {"JIG_FIXTURE_MODE": "bypass"}


# ---------------------------------------------------------------------------
# CLI: jig dev fixtures list/show/clear
# ---------------------------------------------------------------------------


def test_cli_dev_fixtures_list_empty(tmp_path: Path) -> None:
    from click.testing import CliRunner

    from jig.cli import cli

    runner = CliRunner()
    result = runner.invoke(
        cli, ["dev", "fixtures", "list", "--path", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    assert "(no fixtures recorded)" in result.output


def test_cli_dev_fixtures_list_shows_services(tmp_path: Path) -> None:
    import asyncio

    from click.testing import CliRunner

    from jig.cli import cli

    async def _seed() -> None:
        store = FixtureStore(tmp_path)
        sig = request_signature("GET", "https://api.example.com/x", None)
        await store.record(
            FixtureCassette(
                service_id="shopify-api",
                request_signature=sig,
                request={"method": "GET"},
                response={"status": 200},
            )
        )

    asyncio.run(_seed())
    runner = CliRunner()
    result = runner.invoke(
        cli, ["dev", "fixtures", "list", "--path", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    assert "shopify-api" in result.output


def test_cli_dev_fixtures_show_lists_cassettes(tmp_path: Path) -> None:
    import asyncio

    from click.testing import CliRunner

    from jig.cli import cli

    async def _seed() -> None:
        store = FixtureStore(tmp_path)
        sig = request_signature("GET", "https://api.example.com/x", None)
        await store.record(
            FixtureCassette(
                service_id="shopify-api",
                request_signature=sig,
                request={
                    "method": "GET",
                    "url": "https://api.example.com/x",
                },
                response={"status": 200},
            )
        )

    asyncio.run(_seed())
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["dev", "fixtures", "show", "shopify-api", "--path", str(tmp_path)],
    )
    assert result.exit_code == 0, result.output
    assert "GET" in result.output
    assert "https://api.example.com/x" in result.output


def test_cli_dev_fixtures_clear_requires_confirm(tmp_path: Path) -> None:
    import asyncio

    from click.testing import CliRunner

    from jig.cli import cli

    async def _seed() -> None:
        store = FixtureStore(tmp_path)
        await store.record(
            FixtureCassette(
                service_id="shopify-api",
                request_signature="x",
                request={},
                response={},
            )
        )

    asyncio.run(_seed())
    runner = CliRunner()
    result = runner.invoke(
        cli, ["dev", "fixtures", "clear", "shopify-api", "--path", str(tmp_path)]
    )
    assert result.exit_code != 0
    assert "--confirm" in result.output


def test_cli_dev_fixtures_clear_with_confirm_removes(tmp_path: Path) -> None:
    import asyncio

    from click.testing import CliRunner

    from jig.cli import cli

    async def _seed() -> None:
        store = FixtureStore(tmp_path)
        await store.record(
            FixtureCassette(
                service_id="shopify-api",
                request_signature="x",
                request={},
                response={},
            )
        )

    asyncio.run(_seed())
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "dev",
            "fixtures",
            "clear",
            "shopify-api",
            "--confirm",
            "--path",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "cleared" in result.output
    assert not fixture_store_path(tmp_path, "shopify-api").is_file()
