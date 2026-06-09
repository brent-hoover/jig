---
title: Module Boundaries — Implementation Plan
type: plan
status: draft
owner: brent-hoover
created: 2026-06-08
updated: 2026-06-08
design: ./design.md
---

# Module Boundaries — Implementation Plan

## Overview

Seven steps, in dependency order. The schema lands first (additive, no callers). Step 2a resolves the one
blocking open question — the semgrep import-matching pattern set — and lands it as a committed regression
test, *before* step 2b builds `generate_boundary_rules` on the pinned patterns (a pure, standalone function
testable with fixture `boundaries.yaml` files). Step 3 adds the `sa_write_boundaries` MCP tool so the SA
can author boundaries; step 4 wires generation into `arch_finalize` so authored boundaries actually produce
rules. Step 5 adds enforcement at the dev gate — no existing signature changes (`project_path` is derived
from the worktree layout, not threaded). Step 6 is the end-to-end integration. Each step is a standalone
commit/PR; everything is additive and the dev gate is guarded by "no boundary rule files → no-op", so any
single step reverts cleanly.

## Preconditions

- [x] Design approved (`design.md`).
- [ ] **[blocking — hard gate]** `semgrep` is installed and on `PATH` (`semgrep --version` succeeds).
  Step 2a (the blocking pattern spike) cannot start without it, and the step 5 gate needs it; an absent
  semgrep blocks the plan, it doesn't merely degrade.
- [x] Work on a worktree under `.worktrees/`, not `develop` (`.worktrees/sa-architect-phase2`).
- [ ] Boundaries operate on the current module-producing SA path (`sa_incremental_mcp`/`sa_mvp`/bones
  scenarios); no dependency on Phase 2a SA unification or the medium PO-topology decision.

## Steps

### 1. `BoundariesFile` schema (`jig/schemas/arch.py`)

**What:**
- Add `OntologyTerm`, `InternalBoundaries`, `ExternalBoundaries`, `BoundariesFile` models, each with
  `extra="forbid"`. `BoundariesFile.module` is kebab-validated via `validate_kebab_id` (matching the other
  id-bearing arch models); reuse the existing `ChangeLogEntry`.
- Add the four names to `__all__`.

**Why:** Validated foundation every later step builds on. Purely additive — no callers yet.

**Verify:**
- New cases in `tests/test_schemas_arch.py`: valid `BoundariesFile` round-trips; `extra="forbid"` rejects
  an unknown key on each sub-model; non-kebab `module` is rejected; `internal`/`external` default to empty;
  `change_log` accepts `ChangeLogEntry`; `model_dump(sort_keys=False)` re-parses identically (step 3 relies
  on canonical dump for the atomic write).
- `uv run pytest tests/test_schemas_arch.py -q`; `uv run ruff check jig/ && uv run ruff format --check jig/`.

### 2a. Semgrep import-pattern spike (resolves the blocking open question)

**What:** Against the installed semgrep, confirm which pattern forms match each Python import shape — plain
`import pkg`, `from pkg import x`, and nested `import pkg.sub` / `from pkg.sub import x` — using a temp
package fixture. Pin the verified pattern set (or a `pattern-regex` fallback if metavariables don't bind
across dotted paths) as a module-level constant in `jig/boundary_rules.py`, with the finding recorded.

**Why:** The generator's rule template can't be finalized until the pattern coverage is known; a surprising
result may force a regex-based template. Landing this first de-risks step 2b.

**Verify:**
- Committed regression test (`tests/test_boundary_rules.py`): build a temp package, run semgrep with the
  pinned patterns, assert a forbidden import is flagged and an allowed import is not — across all import
  shapes. `uv run pytest tests/test_boundary_rules.py -q`.

### 2b. `generate_boundary_rules` (`jig/boundary_rules.py`)

**What:** Implement `generate_boundary_rules(project_path) -> list[Path]` on the step-2a patterns:
- resolve `top_pkg` from `config.project.name` (kebab/space → `_`, lower) — same derivation as
  `init_workflow.py:1535`;
- resolve **two sets**: the full module-id set (all subdirs of `.jig/spec/modules/`) for allow-list
  compilation + reference validation, and the modules-with-boundaries set (glob `*/boundaries.yaml`). Do
  **not** use `_collect_authored_module_ids` — it keys on `contracts.yaml`, so a boundaries-only module
  would be silently skipped;
- load + validate each boundaries file against `BoundariesFile`;
- **hard-error** on (a) any `internal.allowed_modules`/`forbidden_modules` id not in the full module set,
  (b) a missing `src/<top_pkg>/<m_snake>/` package dir;
- compile internal (allow-list → concrete deny targets using the full module set) + external (forbidden
  only; `external.allowed` advisory) into per-module deny rules using the step-2a patterns;
- clear and fully regenerate `.jig/rules/semgrep/boundaries/`, one `<m>.yml` per module; deterministic
  rule ids `boundary-<m>-(no-internal|no-external)-<target>`.

**Why:** The rule compiler is the core mechanism; isolating it (pure `project_path → files`) makes it
unit-testable before any MCP/gate wiring exists.

**Verify:**
- Unit tests (`tests/test_boundary_rules.py`): allow-list compiles to the right deny targets; external
  forbidden emitted, external allowed ignored; a boundaries-only module (no `contracts.yaml`) **is**
  picked up; unknown module id → raises; missing package dir → raises; idempotency (run twice → identical
  bytes); rule-id determinism.
- `uv run pytest tests/test_boundary_rules.py -q`; lint/format.

### 3. `sa_write_boundaries` MCP tool

**What:**
- `handle_sa_write_boundaries(*, project_path, boundaries: dict)` in `jig/sa_incremental_mcp.py` — validate
  full `BoundariesFile` payload, atomic-write `.jig/spec/modules/<m>/boundaries.yaml` (`model_dump`,
  `sort_keys=False`); return an ack.
- Register the `sa_write_boundaries` `@tool` in `jig/mcp_server.py`.
- Wire into `jig/defaults/roles/sa_mvp.yaml`: add to the machine `allowed_tools` list **and** document it
  in the prose tool catalog (the `module_set_*` block).
- Does **not** call `generate_boundary_rules` (deferred to step 4 / `arch_finalize`).

**Why:** Lets the module-producing SA author boundary declarations. Generation stays deferred so a
half-written set never yields stale rules.

**Verify:**
- Handler tests in `tests/test_sa_incremental_mcp.py`: valid payload writes a schema-valid
  `boundaries.yaml`; invalid payload raises; re-write replaces the file.
- Role-loader test: `sa_write_boundaries` ∈ `load_role(tmp, "sa-mvp").allowed_tools`.
- Registration test in `tests/test_sa_incremental_registration.py`, mirroring its existing pattern.
- `uv run pytest tests/test_sa_incremental_mcp.py tests/test_sa_incremental_registration.py -q`; lint.

### 4. Hook generation into `arch_finalize`

**What:**
- Call `generate_boundary_rules(project_path)` at the tail of `handle_arch_finalize`
  (`sa_incremental_mcp.py:1414`), after the existing module/contract validation. A generation hard-error
  surfaces as the finalize failure (the SA sees it and fixes the offending boundary).

**Why:** Closes the author → rules loop: finalizing an architecture with boundaries produces the
enforcement rules.

**Verify:**
- Integration test: write modules + `boundaries.yaml` via the step-3 handler, run `handle_arch_finalize`,
  assert `.jig/rules/semgrep/boundaries/<m>.yml` exists and matches the generator's output; a bad boundary
  (unknown module id) makes finalize raise.
- `uv run pytest tests/ -q -k "arch_finalize or boundary"`; lint.

### 5. Dev-gate enforcement (`jig/worktree.py`)

**What:**
- Add `_boundary_check(worktree_path)` after `_auto_lint` in `commit_worktree` — **no signature change**.
  Derive `project_path = worktree_path.parents[2]` and assert the `.jig/worktrees/` layout (fail loud if
  the shape is unexpected; never silently skip). This keeps `commit_worktree`, `handle_commit_progress`,
  and `_auto_commit_worktree` untouched.
- `_boundary_check` behavior:
  - no `project_path/.jig/rules/semgrep/boundaries/*.yml` → no-op;
  - `semgrep` not on `PATH` (`shutil.which`) → visible warning, return (loud-degrade, no pass-as-clean);
  - else run `semgrep --metrics off --json --quiet --config <abs boundaries dir> <worktree>`; exit `0` →
    pass, `1` → raise `BoundaryViolationError(violations=[...])` (messages from rule id + finding), `>=2` →
    visible warning (degrade), not a violation.
- Add `BoundaryViolationError`; surface it on the same gate-failure path as `LintError`. Run the check
  before the code-metrics computation.

**Why:** Turns generated rules into an actual gate — a forbidden import fails the dev commit and loops back
to the agent.

**Verify:**
- Unit tests (`tests/test_worktree_boundary_gate.py`, new): worktree with a forbidden import + matching
  rules → `BoundaryViolationError`; compliant worktree → passes; no rules → no-op; semgrep missing
  (monkeypatch `shutil.which`) → warns, no raise; semgrep exit ≥2 (stub) → warns, no
  `BoundaryViolationError`; an unexpected worktree layout → asserts loudly.
- `uv run pytest tests/ -q -k "worktree or commit_worktree or boundary"`; lint.

### 6. End-to-end integration + full verification

**What:** Exercise the whole author → generate → scaffold → gate → fix loop, and run the complete gate.
This is the only design success-criterion not covered by a unit/integration test in steps 1–5.

**Why:** Confirms the components compose into the behavior the problem statement requires — a forbidden
import actually fails a real dev commit.

**Verify:**
- End-to-end: in a temp/bones project, author boundaries (module A forbids B) via the step-3 tool, finalize
  (step 4 generates rules), put an `import <pkg>.b` in A's package, run the dev-commit gate, and confirm it
  raises `BoundaryViolationError` naming A→B; remove the import and confirm the gate passes.
- `uv run ruff check jig/ tests/` and `uv run ruff format --check jig/`; `uv run pytest tests/ -q` — full
  suite green.

## Rollback

Every step is additive: new schema (1), new module + pinned patterns (2a/2b), new tool (3), a tail call in
`arch_finalize` guarded by "no boundaries → nothing to generate" (4), and an internal `_boundary_check`
step in `commit_worktree` with no signature change (5). The dev gate is guarded by "no boundary rule files
→ no-op", so even mid-rollout a project without boundaries is unaffected, and reverting any single commit
restores prior behaviour.

## Out of scope for this plan

- SA unification (Phase 2a), medium PO topology, activating module production in default profiles.
- Non-Python enforcement; runtime/import-hook enforcement.
- Positive `external.allowed` enforcement; ontology-term machine-checking.
- Contract↔boundary consistency checks and a federation boundary reviewer (design's Optimal).

## Progress

- [ ] Step 1: `BoundariesFile` schema
- [ ] Step 2a: Semgrep import-pattern spike (blocking open question)
- [ ] Step 2b: `generate_boundary_rules`
- [ ] Step 3: `sa_write_boundaries` MCP tool
- [ ] Step 4: Hook generation into `arch_finalize`
- [ ] Step 5: Dev-gate enforcement
- [ ] Step 6: End-to-end integration + full verification

## Change log

- 2026-06-08: Initial draft (brent-hoover)
- 2026-06-08: Revised ×1 (plan-reviewer) — split step 2 into 2a (blocking semgrep pattern spike) + 2b
  (generator); fix module discovery to glob `*/boundaries.yaml` + use all module dirs (not the
  `contracts.yaml`-keyed `_collect_authored_module_ids`); step 5 derives `project_path` from the worktree
  layout (no `commit_worktree` signature change / no call-site churn); promote semgrep presence to a hard
  precondition gate; scope step 6 to the end-to-end loop.
