---
name: ruff
description: "How to use ruff for Python linting and formatting"
applies_to:
  language: python
---

# ruff

- Format: `uv run ruff format .`
- Lint: `uv run ruff check .`
- Fix safely: `uv run ruff check --fix .`
- Configuration lives in `pyproject.toml` under `[tool.ruff]`.
