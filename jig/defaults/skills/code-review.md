# Code Review

- Check that the implementation matches the design doc
- Verify all tests pass: run the test command from project context
- Run linters (ruff) and type checkers (mypy) if configured
- Look for: security issues, error handling gaps, missing edge cases
- Check for hardcoded values that should be configurable
- Verify no debug code, print statements, or commented-out code left behind
- Check that new public APIs have docstrings
- If issues are found, report them clearly with file paths and line numbers
