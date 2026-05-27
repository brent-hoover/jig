# Project notes

FastAPI service scaffolded from jig's `fastapi` template. The SA will fill in the
project-specific sections below as the design takes shape.

## Stack

- Python 3.12+, uv-managed.
- FastAPI on uvicorn (`uvicorn[standard]`).
- pydantic v2 for request/response models.
- pytest + pytest-asyncio for tests (`asyncio_mode = "auto"`).
- httpx for the test client (`httpx.AsyncClient` + `ASGITransport`).
- ruff + mypy for lint and types.

## Layout

```
src/<package>/
  __init__.py
  app.py            # FastAPI app instance (`app = FastAPI(...)`)
  ...               # routers, models, dependencies as the design evolves
tests/              # pytest suite — endpoint tests use httpx async client
pyproject.toml
Dockerfile          # If present, the deploy target
```

`<package>` is the snake-cased `[project] name`. The FastAPI app instance lives at
`<package>.app:app` (module `app.py`, attribute `app`) — that's the import path uvicorn
and the test client need.

## Common commands

```bash
uv sync                                          # Install / refresh deps
uv run pytest tests/ -v                          # Run the test suite
uv run ruff check .                              # Lint
uv run ruff format .                             # Format
uv run mypy src                                  # Type-check
uv run uvicorn <package>.app:app --reload        # Local dev server (port 8000)
```

For new dependencies use the `add_dependency` MCP tool — don't edit `pyproject.toml`
directly or run `uv add` yourself.

## Conventions

- **Async by default for I/O.** Route handlers, database calls, HTTP calls — `async def`.
  Sync work goes through `asyncio.to_thread` so the event loop isn't blocked.
- **pydantic v2 models for everything crossing the boundary.** Request bodies, response
  shapes, settings. Avoid passing raw dicts.
- **Test handlers via httpx async client + ASGITransport**, not TestClient — keeps the
  test suite asyncio-native and consistent with production code:

  ```python
  async with httpx.AsyncClient(
      transport=httpx.ASGITransport(app=app), base_url="http://test"
  ) as client:
      r = await client.get("/health")
  ```

## What this service does

<!-- SA: replace with 1-2 sentences describing the service's purpose. -->

TBD.

## Endpoints and routers

<!-- SA: list the public surface, e.g.
     - `GET /health` — liveness probe
     - `POST /widgets` — create a widget (router: src/<package>/routers/widgets.py) -->

TBD.

## Deploy

<!-- SA: deploy target = container per template metadata. Note image base, registry, env
     vars expected, secrets references. Don't put actual secret values here. -->

TBD.

## Common pitfalls

<!-- Examples worth recording:
     - "Healthcheck pings X external service — flakes when Y"
     - "Migration N changed schema Z; older clients will fail on field W" -->

- None recorded yet.
