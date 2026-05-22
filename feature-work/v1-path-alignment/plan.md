---
title: v1 SA Path Alignment — Implementation Plan
type: plan
status: draft
owner: brent
created: 2026-05-21
updated: 2026-05-21
design: ./problem.md
---

# v1 SA Path Alignment — Implementation Plan

## Overview

Move v1 SA's structured-spec and architecture outputs from `docs/` to `.jig/spec/` so they match
where every reader already looks. One PR, mechanical rename plus prompt + test updates. No new
behavior, no schema changes, no migration.

## Preconditions

- [x] Problem captured: [problem.md](./problem.md)
- [x] Approach approved: keep `sa.yaml`, only fix paths (no SA consolidation)
- [x] No active downstream work depends on `docs/` paths (project-profiles design assumes v2 layout)

## Steps

### 1. Move the canonical write paths

**What:**

- `jig/init_mcp.py:39-44` — change both functions:
  - `_spec_path(project_path)` → `project_path / ".jig" / "spec" / "project.structured.yaml"`
  - `_arch_path(project_path)` → `project_path / ".jig" / "spec" / "architecture.yaml"`
- Verify `atomic_write_text` and `_spec_path`'s callers create parent directories (mkdir parents=True).

**Why:** This is the single source of truth for v1 SA's outputs. Everything else is downstream of this.

**Verify:** Direct unit test — call `handle_arch_set_field` with a tmp project_path; assert the file lands
at `<tmp>/.jig/spec/architecture.yaml`. Run existing `tests/test_init_mcp.py` (if any) and confirm
no path assertions break beyond the ones step 4 updates.

### 2. Update the init-workflow callers

**What:**

- `jig/init_workflow.py:513` — display string `target/docs/project.structured.yaml` →
  `target/.jig/spec/project.structured.yaml`
- `jig/init_workflow.py:515` — display string `target/docs/architecture.yaml` →
  `target/.jig/spec/architecture.yaml`
- `jig/init_workflow.py:1252` — `arch_file = project_path / "docs" / "architecture.yaml"` →
  `project_path / ".jig" / "spec" / "architecture.yaml"`. This is the scaffold-finalize step that
  preserves any SA-authored fields when copying the template; the path must match where the SA
  actually wrote the file.

**Why:** The scaffold-finalize step is the only other Python writer of the architecture file. The
display strings are user-visible at end-of-init; they should point at the real files.

**Verify:** Run a `jig init` on a fresh scratch dir with `--brief` (synthetic-operator answers).
Confirm the printed summary points at `.jig/spec/...` paths and the files actually exist at those
paths. Confirm no file lands at `docs/architecture.yaml` or `docs/project.structured.yaml`.

### 3. Update the L0 PO handoff declaration

**What:**

- `jig/po_l0_mcp.py:150` — `outputs=["docs/project.structured.yaml"]` →
  `outputs=[".jig/spec/project.structured.yaml"]`

**Why:** The L0 PO declares its outputs in the handoff so downstream agents know what was produced.
Stale path → misleading handoff metadata.

**Verify:** `tests/test_po_l0_mcp.py:199` — update the assertion in the same commit (step 4) and
confirm it passes.

### 4. Update the `sa.yaml` role prompt

**What:**

- `jig/defaults/roles/sa.yaml:16-17` — replace
  > "structured spec at `docs/project.structured.yaml` is your authoritative input. You write
  > your decisions to `docs/architecture.yaml`"

  with the `.jig/spec/` equivalents. The text at line 11-12 (which already says `.jig/spec/`)
  becomes the single, consistent statement.

**Why:** Eliminates the internal contradiction. Future SA agents read consistent guidance.

**Verify:** `grep -n "docs/architecture\|docs/project\.structured" jig/defaults/roles/sa.yaml` returns
no matches. The remaining `docs/brief.md` reference is intentional (brief stays at `docs/`).

### 5. Update tests + scenarios

**What:**

- `tests/test_sim_bones_scenario.py:102` — path string updates.
- `tests/test_po_l0_mcp.py:199` — `assert "docs/project.structured.yaml" in handoffs[0].outputs` →
  `assert ".jig/spec/project.structured.yaml" in handoffs[0].outputs`.
- `tests/scenarios/bones-walking-skeleton.scenario.yaml:54` — `path: docs/project.structured.yaml` →
  `path: .jig/spec/project.structured.yaml`.
- Sweep `tests/` for any other hardcoded `docs/architecture` or `docs/project.structured` strings
  that grep missed (likely none, but verify).

**Why:** Tests that pin the old paths will fail after step 1. Update them in lockstep so the suite
goes green in the same commit.

**Verify:** `uv run pytest tests/` — full suite, expect 3922+ passed (no new failures introduced).

### 6. Verify the full init flow end-to-end

**What:** Run `jig init` against a scratch project with the eval `--brief` flag.

```bash
mkdir /tmp/jig-path-test
cd /tmp/jig-path-test
uv run --directory /Users/brent/Projects/personal/jig \
    jig init --name jig-path-test --brief /tmp/test-brief.md --auto
ls -la .jig/spec/ docs/
```

Expected:
- `.jig/spec/project.structured.yaml` exists
- `.jig/spec/architecture.yaml` exists
- `docs/brief.md` exists (unchanged)
- `docs/architecture.yaml` does NOT exist
- `docs/project.structured.yaml` does NOT exist

**Why:** Unit tests pin individual call sites; an end-to-end run confirms the integration is
coherent and no missed call site silently writes to the old location.

**Verify:** Listed checks above plus `jig plan` runs successfully against the resulting project
(precondition check at `cli.py:210` now finds the file where it expects).

### 7. Commit and PR

**What:** Single commit on `refactor/v1-path-alignment`. Conventional commit subject:

```
refactor(init): Move v1 SA outputs from docs/ to .jig/spec/
```

Body explains the divergence, lists the call sites updated, references the hn-cli analyzer finding
that motivated the work.

**Verify:** PR opened against `develop` with:
- Problem/Fix section linking back to the run-38efa7d7 analyzer recommendations
- Manual test steps from step 6
- Note that existing eval projects (hn-cli) need re-init to regenerate at new paths (no migration)

## Rollback

Single commit, fully reversible. `git revert` restores the `docs/` paths. No data loss — the
files are regenerable from `docs/brief.md` via `jig init` and existing eval-fixture state is
expendable per `feedback_no_migration_needed`.

## Out of scope for this plan

- Project profiles (`small` / `medium`) — separate PR, depends on this landing first
- Consolidating `sa.yaml` and `sa_mvp.yaml` into one role — explicitly decided against (keeping
  both)
- Migrating existing hn-cli eval fixture from `docs/` to `.jig/spec/` — re-init regenerates
- Updating reviewer prompts — they already expect `.jig/spec/`; no change needed
- Updating the `cli.py:210` precondition check — already correct for `.jig/spec/`

## Change log

- 2026-05-21: Initial draft (brent)
