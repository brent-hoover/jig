# Testing

- Use pytest as the test framework
- Place tests in `tests/` directory, mirroring `src/` structure
- Name test files `test_<module>.py` and test functions `test_<behavior>`
- Use fixtures for setup/teardown, prefer function-scoped fixtures
- Test behavior, not implementation — test the public API
- Include edge cases: empty inputs, boundary values, error conditions
- Use `pytest.raises` for expected exceptions
- Keep tests independent — no test should depend on another test's state
- Run the full test suite and verify all tests pass before reporting completion
