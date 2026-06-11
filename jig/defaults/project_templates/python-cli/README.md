# my-project

Python CLI tool scaffolded from the `python-cli` template.

## Run it

```bash
uv sync
uv run my-project --help
```

## Layout

- `src/myproject/cli.py` — `typer.Typer()` app. Add commands here.
- `src/myproject/transport.py` — `Transport` wraps `httpx.AsyncClient`. Constructor takes a client
  so tests can inject a stub; the default factory creates one with sensible timeouts.
- `src/myproject/__main__.py` — supports `python -m myproject` alongside the `my-project` entry point.
- `tests/test_smoke.py` — asserts `--help` works via `typer.testing.CliRunner` (no install needed).

## Test

```bash
uv run pytest
```
