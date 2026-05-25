---
title: Add ruff checks to dev completion phase — Implementation Plan
type: plan
status: draft
owner: Brent Hoover
created: 2026-05-25
updated: 2026-05-25
design: ./problem.md
---

# Add ruff checks to dev completion phase — Implementation Plan

## Overview

Add `ruff-check` to the `automated_checks` for every `test` and `implement` phase across all
default workflow files, then update the `dev` and `test` role prompts to tell agents the gate
exists. Workflows first so the gate is in place, then prompts so agents know to expect it.

## Preconditions

- [x] Problem statement approved.
- [x] `ruff-check` already exists in `jig/defaults/checks.yaml` as a required check — no new
  check definition needed.

## Steps

### 1. Add `ruff-check` to implement and test phases in all workflow files

**What:** Edit all workflow files that have a `test` or `implement` phase:
`default.yaml`, `feature-s.yaml`, `feature-s-full.yaml`, `bugfix.yaml`, `migration.yaml`,
`perf.yaml`, `refactor.yaml`, and `spike.yaml`. Add `ruff-check` to `automated_checks` on
each matching phase.

**Why:** This is the gate. Without it, ruff violations are not caught until validate.

**Verify:** `grep -c "ruff-check" jig/defaults/workflows/*.yaml` — every file with a test or
implement phase should show a count ≥ 1.

### 2. Update `dev.yaml` and `test.yaml` role prompts

**What:** Add one sentence to each prompt noting that `ruff-check` runs as a phase gate and
must pass before the ticket can be resolved.

**Why:** Agents need to know the gate exists so they can fix violations before calling
`update_ticket`, rather than being surprised by a bounce.

**Verify:** Read both files and confirm the ruff note is present.

## Rollback

Revert the workflow YAML edits and prompt changes — no data migration, no schema change.

## Out of scope for this plan

- Adding `mypy` or other linters to earlier phases.
- Changing the validate phase checks (already correct).

## Change log

- 2026-05-25: Initial draft (Brent Hoover)
