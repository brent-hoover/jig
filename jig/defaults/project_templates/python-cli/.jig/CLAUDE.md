# Project notes

Python CLI scaffolded from jig's `python-cli` template. The SA will fill in the
project-specific sections below as the design takes shape.

## Stack

- Python 3.12+, uv-managed.
- typer for the CLI surface.
- httpx for HTTP (use the async client for I/O-bound work).
- pytest + pytest-asyncio for tests.
- ruff + mypy for lint and types.

## Layout

```
src/<package>/
  __init__.py
  cli.py            # typer app — entry point is exposed via [project.scripts]
tests/              # pytest suite
pyproject.toml      # `[project.scripts] <package> = "<package>.cli:app"`
```

`<package>` is the snake-cased `[project] name`. The installed CLI command name comes from
`[project.scripts]` in `pyproject.toml`.

## Common commands

```bash
uv sync                       # Install / refresh deps
uv run pytest tests/ -v       # Run the test suite
uv run ruff check .           # Lint
uv run ruff format .          # Format
uv run mypy src               # Type-check
uv run <package> --help       # Smoke test the CLI (post-install)
```

For new dependencies use the `add_dependency` MCP tool — don't edit `pyproject.toml`
directly or run `uv add` yourself.

## What this CLI does

<!-- SA: replace with 1-2 sentences describing the CLI's purpose. -->

TBD.

## Entry points and subcommands

<!-- SA: list the typer commands and what each does. Example:
     - `<package> init <path>` — bootstrap a new X
     - `<package> status` — print the current Y -->

TBD.

## Common pitfalls

<!-- Examples of things worth recording here:
     - "The X subcommand needs an env var Y set"
     - "Async commands need `asyncio.run` because typer doesn't await coroutines" -->

- None recorded yet.
