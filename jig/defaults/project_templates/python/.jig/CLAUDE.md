# Project notes

Python project scaffolded from jig's `python` template. The SA will fill in the
project-specific sections below as the architecture takes shape; agents working on later
tickets should read this file before touching code.

## Stack

- Python 3.12+, uv-managed.
- pytest for tests (`testpaths = ["tests"]`).
- ruff for lint + format.
- mypy for type checks.
- hatchling build backend.

## Layout

```
src/<package>/      # Production code; imported as `<package>` after `uv pip install -e .`
tests/              # pytest suite — mirrors src/ structure
pyproject.toml      # Dependencies, test config, lint config
```

`<package>` is the value in `pyproject.toml`'s `[project] name` (snake-cased).

## Common commands

```bash
uv sync                       # Install / refresh deps
uv run pytest tests/ -v       # Run the test suite
uv run ruff check .           # Lint
uv run ruff format .          # Format
uv run mypy src               # Type-check production code
```

Add new dependencies via `add_dependency` (the jig MCP tool); do not edit `pyproject.toml`
directly or run `uv add` yourself.

## What this project does

<!-- SA: replace with 1-2 sentences explaining the project's purpose. -->

TBD — set during SA scaffolding.

## How to run / verify locally

<!-- SA: replace with the actual run command(s) for this project. For a library this might
     just be the test suite; for a CLI/service, the entry point. -->

TBD.

## Common pitfalls

<!-- SA + later contributors: add gotchas as they're discovered (e.g. "the X test needs
     network access and is skipped in CI", "module Y is auto-generated — don't edit by
     hand"). -->

- None recorded yet.
