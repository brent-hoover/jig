# Validation

Your job is to verify the application actually works, not just that tests pass.

## Process

1. **Read the design doc** (`docs/design.md`) and find the **Validation Steps** section
2. **Execute each validation step** exactly as described — run the commands, check the output
3. **Run the test suite** as well (`uv run pytest` or the configured test command)
4. **Run linters and type checkers** if configured (`uv run ruff check .`, `uv run mypy .`)
5. **Fix any issues found** — but do not add new features or change behavior

## Rules

- If a validation step fails, diagnose why and fix the code
- If a validation step is ambiguous or impossible, report it via `report_completion` with `needs_info`
- If tests fail, fix the code (not the tests) unless the test is clearly wrong per the design doc
- Report what you validated, what passed, and what you had to fix
