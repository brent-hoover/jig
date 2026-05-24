---
name: python
description: "Python coding patterns and conventions for this project"
applies_to:
  language: python
---

# Python conventions

- Follow PEP 8. Prefer explicit over implicit.
- Use type hints on public function signatures.
- Use `pathlib.Path` for filesystem paths, not `os.path`.
- Use f-strings for formatting, not `%` or `.format()`.
