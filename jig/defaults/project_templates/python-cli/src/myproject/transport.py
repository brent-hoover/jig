"""Injectable HTTP transport. Constructor takes an ``httpx.AsyncClient``
so tests can hand in a stub (with a mock transport, fixture replay,
etc.) without monkeypatching."""

from __future__ import annotations

from types import TracebackType

import httpx


class Transport:
    """Thin wrapper around ``httpx.AsyncClient`` for the project's HTTP calls.

    Use as an async context manager so the underlying client is always
    closed::

        async with Transport.default() as t:
            data = await t.get_json("https://...")
    """

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    @classmethod
    def default(cls) -> Transport:
        """Construct with a sensible-default async client. Production code paths
        use this; tests construct ``Transport(client=...)`` directly."""
        return cls(httpx.AsyncClient(timeout=httpx.Timeout(10.0)))

    async def __aenter__(self) -> Transport:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def get_json(self, url: str) -> dict[str, object]:
        """Fetch JSON. Raises ``httpx.HTTPStatusError`` on 4xx/5xx;
        raises ``TypeError`` if the response body isn't a JSON object."""
        response = await self._client.get(url)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise TypeError(f"expected JSON object, got {type(data).__name__}")
        return data

    async def aclose(self) -> None:
        await self._client.aclose()
