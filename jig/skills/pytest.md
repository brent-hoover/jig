---
name: pytest
applies_to:
  language: python
---

# pytest conventions

- Test files live in `tests/` and are named `test_*.py`.
- Test functions start with `test_`.
- Use `pytest.fixture` for setup, not `unittest` classes.
- Use `pytest.mark.asyncio` for async tests (requires `pytest-asyncio`).
- Run a single test: `uv run pytest tests/test_foo.py::test_bar -v`.
