---
name: typescript
description: "TypeScript coding patterns and conventions for this project"
applies_to:
  language: typescript
---

# TypeScript conventions

- Prefer `const` over `let`; never use `var`.
- Use explicit return types on exported functions.
- Use `unknown` over `any` for untrusted values.
- Module format is ESM (`import`/`export`), not CommonJS (`require`).
