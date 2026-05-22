---
title: v1 SA Path Alignment — Problem Statement
type: problem
status: draft
owner: brent
created: 2026-05-21
updated: 2026-05-21
---

# v1 SA Path Alignment — Problem Statement

## Context

Jig has two SA (Systems Architect) roles in active use:

- **`sa` (v1)** — used by single-module projects via `jig init`. Writes its outputs (`project.structured.yaml`,
  `architecture.yaml`) under `docs/` in the project workspace.
- **`sa_mvp` (v2)** — used by multi-module / multi-suite projects. Writes its outputs (`architecture.yaml`,
  per-module `contracts.yaml`, suites, ontology) under `.jig/spec/` in the project workspace.

Everything downstream — `jig/cli.py:210`'s precondition check, `jig/spec_loader.py`, `jig/sa_mcp.py`,
`jig/sa_incremental_mcp.py`, `jig/graph/derive.py`, `jig/schemas/arch.py`, all reviewer role prompts,
the PM role prompt — assumes the v2 layout (`.jig/spec/`). Only the v1 SA writes to `docs/`.

## Problem

The v1 SA's write paths are out of step with every reader in the system. Concrete consequences observed
on the hn-cli eval run (run id `38efa7d7`, 2026-05-20):

1. **Reviewers run `find .jig/spec -name contracts.yaml` and `.jig/spec -name architecture.yaml`** —
   both return nothing on a v1 project because the files live under `docs/`. The hn-cli analyzer found
   35+ identical failed lookups across reviewer spawns. The graceful-skip patch
   (`fix/contracts-yaml-missing-graceful`, commit `efd9eb1`) stops the cascading failure but does not
   eliminate the wasted lookup.

2. **`reviewer-architectural` found `docs/architecture.yaml` on cycle 1 of c8ee7ae1 but missed it on
   cycles 2–3** (same agent type, fresh instances) — indicates inconsistent fallback search behavior
   when the canonical `.jig/spec/` path is empty.

3. **`jig plan` precondition check** at `jig/cli.py:210` tests
   `(jig_dir / "spec" / "architecture.yaml").is_file()` — wrong path for v1 projects. Would falsely
   report "Project not initialized" if `jig plan` were invoked on a v1 project. (Latent — the hn-cli
   run used the orchestrator, which doesn't hit this check.)

4. **`sa.yaml` self-contradicts**: line 11–12 say "read `.jig/spec/project.structured.yaml` and write
   `.jig/spec/architecture.yaml`", line 16–17 say "structured spec at `docs/project.structured.yaml`
   is your authoritative input. You write your decisions to `docs/architecture.yaml`". The
   implementation in `jig/init_mcp.py:_arch_path` follows the second pair — but a future SA agent
   reading the role description has 50/50 odds of writing to the wrong place.

## Simplest possible solution

Change `_spec_path` and `_arch_path` in `jig/init_mcp.py` to write under `.jig/spec/` instead of
`docs/`. Update all callers and tests that hardcode the `docs/` paths. Resolve the `sa.yaml`
contradiction by keeping the `.jig/spec/` half. Brief stays at `docs/brief.md` — that's user-facing
markdown, v2 keeps it there too.

## Complications considered

- **Scale**: N/A — this is mechanical path replacement; no runtime behavior change.

- **Concurrency**: N/A — `atomic_write_text` already handles concurrent writes; only the target
  path changes.

- **Failure modes**: A partial migration (some writes still going to `docs/`, some to `.jig/spec/`)
  would leave the orchestrator unable to find architecture artifacts. Mitigation: do the rename
  atomically in one commit; tests pin the new path; no compatibility shim that reads either location.

- **Cross-cutting policies**: N/A.

- **Migration**: Existing v1 projects (e.g. the hn-cli eval fixture) have files at `docs/`. Per the
  `feedback_no_migration_needed` memory and project convention, jig has no production deployments —
  existing eval projects re-init from brief to regenerate. No migration scripts.

- **Coupling to project-profiles**: This work stands on its own; project-profiles
  ([../project-profiles/](../project-profiles/problem.md)) depends on the v1 SA still being viable
  for the `small` profile, which this change preserves. Project-profiles can land after this
  without changes here.

## What this does NOT change

- The v1 SA role (`sa.yaml`) stays. Tools (`arch_set_field`, `sa_propose_scaffold`) stay.
- The v2 SA roles (`sa_mvp`, `sa_v2`) are untouched.
- Brief stays at `docs/brief.md`.
- No new file types, no schema changes.
