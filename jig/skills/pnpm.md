---
name: pnpm
applies_to:
  language: typescript
  package_manager: pnpm
---

# pnpm conventions

- Install deps: `pnpm install`.
- Add a dep: `pnpm add <pkg>`. Dev dep: `pnpm add -D <pkg>`.
- Run a script: `pnpm <script>` (not `pnpm run <script>`).
- `pnpm-lock.yaml` is checked in.
