# Python Conventions

- Use type hints on all function signatures
- Prefer `pathlib.Path` over `os.path`
- Use `snake_case` for functions/variables, `PascalCase` for classes
- Use f-strings for string formatting
- Imports: stdlib first, then third-party, then local — separated by blank lines
- Use `from __future__ import annotations` only if needed for forward refs
- Prefer dataclasses or Pydantic models over plain dicts for structured data
- Use `if __name__ == "__main__":` guard in entry point modules
