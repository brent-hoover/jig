---
name: vitest
applies_to:
  language: typescript
---

# vitest conventions

- Test files are named `*.test.ts` and colocated with source, or live in `tests/`.
- Run all tests: `pnpm test`.
- Run a single file: `pnpm test path/to/file.test.ts`.
- Use `describe`/`it` for grouping; `expect(x).toBe(y)` for assertions.
