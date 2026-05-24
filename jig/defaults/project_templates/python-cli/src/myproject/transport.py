"""Injectable HTTP transport. Constructor takes an ``httpx.AsyncClient``
so tests can hand in a stub (with a mock transport, fixture replay,
etc.) without monkeypatching."""

from __future__ import annotations

import httpx


class Transport:
    """Thin wrapper around ``httpx.AsyncClient`` for the project's HTTP calls."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    @classmethod
    def default(cls) -> Transport:
        """Construct with a sensible-default async client. Production code paths
        use this; tests construct ``Transport(client=...)`` directly."""
        return cls(httpx.AsyncClient(timeout=httpx.Timeout(10.0)))

    async def get_json(self, url: str) -> dict[str, object]:
        """Fetch JSON. Raises ``httpx.HTTPStatusError`` on 4xx/5xx."""
        response = await self._client.get(url)
        response.raise_for_status()
        data = response.json()
        assert isinstance(data, dict)
        return data

    async def aclose(self) -> None:
        await self._client.aclose()
