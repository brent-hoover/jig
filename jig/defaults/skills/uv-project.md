# Working with uv Projects

- Use `uv` as the package manager — never use pip directly
- Add dependencies: `uv add <package>`
- Add dev dependencies: `uv add --group dev <package>`
- Run commands through uv: `uv run pytest`, `uv run ruff check .`, `uv run mypy .`
- Sync environment: `uv sync`
- The project is defined in `pyproject.toml` — edit it for project metadata
- Virtual environment is at `.venv/` and managed by uv automatically
