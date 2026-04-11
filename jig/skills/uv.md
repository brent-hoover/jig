---
name: uv
applies_to:
  language: python
  package_manager: uv
---

# uv conventions

- Always run Python through `uv run python`, never bare `python`.
- Always run tests through `uv run pytest`, never bare `pytest`.
- Add dependencies with `uv add <pkg>`, never `pip install`.
- `uv.lock` is checked in; regenerate with `uv lock` if needed.
