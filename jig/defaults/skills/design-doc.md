# Writing Design Documents

- Start by reading any existing code and docs in the project to understand context
- Structure the design doc with these sections:
  1. **Overview** — what problem this solves and why
  2. **Requirements** — what must be true when done (functional and non-functional)
  3. **Design** — components, data models, interfaces, key decisions
  4. **File Structure** — what files will be created or modified
  5. **Testing Strategy** — what tests are needed and how they verify requirements
  6. **Validation Steps** — concrete, executable steps to verify the feature works end-to-end. These should be specific commands or actions someone (or an agent) can run to confirm the feature behaves correctly. Examples:
     - "Run `uv run python -m myproject add-task 'Buy groceries'` and verify it prints a confirmation with a task ID"
     - "Run `uv run python -m myproject list-tasks` and verify the task created above appears"
     - "Run `uv run python -m myproject add-task ''` and verify it exits with an error message"
     - "Verify that `tasks.json` exists after adding a task and contains valid JSON"
- Be specific: name files, functions, classes, and their responsibilities
- Call out trade-offs and alternatives considered
- Save the design doc as `docs/design.md` in the project
