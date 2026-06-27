---
title: Medium L0–L3 PO Pipeline — Implementation Plan
type: plan
status: superseded
superseded_by: ../../architecture/plan.md
owner: brent-hoover
created: 2026-06-09
updated: 2026-06-26
design: ./design.md
---

# Medium L0–L3 PO Pipeline — Implementation Plan

> **Superseded — do not implement from this doc.** Absorbed into
> `architecture/plan.md` (Epic 6 — Authoring engines): the Discovery interview
> replaces the v1/v2 L0–L3 split (Epic 6 Bones 4). This plan is retained for
> historical context only; follow `architecture/plan.md`, not the steps below.

## Overview

Five steps, ordered so `develop` stays shippable throughout — the medium L0→L3 path only *activates* at
step 3, where the `classify_resume` branch and the `medium.yaml` `sa_role` flip land together. Step 1 moves
profile selection up front (retiring the interactive PM-1 pass) but leaves behavior otherwise unchanged
(medium still resolves `sa_role: sa` until step 3). Step 2 adds the spawn helpers + shared level resolver
as pure, unit-tested units not yet reachable from the loop. Step 3 wires the medium branch end-to-end.
Step 4 rebases the manual `/init --proceed` onto the same shared resolver. Step 5 is the end-to-end medium
auto-init scenario + full verification.

## Preconditions

- [x] Design approved (`design.md`); PM-1-retirement decision approved.
- [x] Work on a worktree under `.worktrees/`, not `develop` (`.worktrees/sa-architect-phase2a`).
- The L0–L3 roles, `po_l0_mcp`–`po_l3_mcp` handlers, and `sa_mvp.yaml` already exist and are tested — this
  is wiring, not a rebuild.

## Steps

### 1. Up-front size selection + PM-1 retirement

**What:**
- Add `ask_project_size(*, console) -> str` to the `PromptHandler` Protocol (`init_prompts.py`) and all
  three impls: `CliPromptHandler` (the guided small/medium prompt + decision framework from design §1),
  `AutoPromptHandler` (returns `"small"` defensively), and the TUI handler (`tui/tui_prompts.py`,
  round-trip).
- In `run_init` (`init_workflow.py`), before the PO/resume loop: **resume-safe** — `run_init` is also the
  resume entrypoint, so prompt/apply only when **no profile is persisted yet** (`load_config(target)
  .profile.name` empty). A fresh init with no `--profile` calls `ask_project_size` then applies via
  `apply_profile` + `save_config`; `--profile` overrides; a **resumed** init (profile already persisted,
  no `--profile`) preserves the first-run choice untouched — re-prompting would overwrite it and could
  switch the PO topology mid-run.
- This makes `cfg.profile.name` always set before the PO, so the v1 PM-1 gate
  (`PM_PROFILE_PASS`/`PM_PROFILE_CONFIRM_PROMPT`) no longer fires for interactive init. Update the small
  init test(s)/scenario that exercised the PM-1 profile pass to the up-front-profile path.

**Why:** Resolves the topology decision before the PO (design §1–2). Self-contained: medium still resolves
`sa_role: sa` (flipped in step 3), so behavior is otherwise unchanged.

**Verify:**
- Unit: `CliPromptHandler.ask_project_size` returns `small`/`medium` for the parsed inputs; `--profile`
  bypasses the prompt (no call); `AutoPromptHandler` returns `small`.
- Seam (the **interactive** path — the new code, not the pre-existing `--profile` bypass): a `run_init`
  test driving `ask_project_size` via a stub `CliPromptHandler` confirms the chosen profile is persisted
  before the first PO spawn and that `PM_PROFILE_PASS`/`PM_PROFILE_CONFIRM_PROMPT` are unreachable on that
  path (both for a `small` and a `medium` choice).
- Update + pass the existing small init test/scenario (now without the PM-1 profile pass).
- **Resume-safe test**: a project with a persisted profile, re-entered via `run_init` with no `--profile`,
  must NOT call `ask_project_size` and must preserve the persisted profile (regression for the resume
  overwrite).
- Guard the eval path: `--brief` without `--profile` raises (eval must pin the profile). Additionally,
  **`--brief --profile medium` is rejected** — a single baked brief can't supply the L0–L3 inputs `sa_mvp`
  needs, and the medium branch ignores the v1 `brief` ticket the baked brief seeds. Tests for both
  rejections (medium evals use the scenario harness, step 5).
- `uv run ruff check jig/ && uv run ruff format --check jig/`; `uv run pytest tests/ -q -k "init or prompt or profile"`.

### 2. L0–L3 spawn helpers + shared level resolver (not yet wired)

**What:**
- Add `run_po_l0_conversation`, `run_po_l1_conversation`, `run_po_l2_conversation`,
  `run_po_l3_conversation(*, suite_id)` in `init_workflow.py`, mirroring `run_po_conversation` (~L866):
  each ensures its level ticket (`project` / `discovery` / `suites` / `suite-<id>`), loads the matching
  `po-l*` role, spawns the agent, streams to the console.
- Add `next_incomplete_level(project_path, tickets, threads) -> NextLevel | None`, where `NextLevel`
  carries `level`, `ticket_id`, and `suite_id` (the **bare** suite id for L3 = `ticket_id` minus the
  `suite-` prefix; `None` for L0–L2). Resolves the next incomplete level via **thread markers** — a
  `Handoff` on each level's ticket (`project`→`po-l1`, `discovery`→`po-l2`, `suites`→`po-l3`,
  `suite-<id>`→`sa`), iterating suites from `suites.yaml` for L3. Returns `None` when all levels (incl.
  every suite's L3) are done.

**Why:** The compiler/driver pieces, isolated and unit-testable before any `classify_resume` wiring. Not
reachable from the run loop yet, so additive and safe.

**Verify:**
- Unit tests for `next_incomplete_level`: returns L0 on a bare project; L1 after an L0 handoff; L2 after L1;
  L3-for-suite-X after L2 with X pending — asserting `suite_id == "X"` (the **bare** id, not the
  `suite-X` ticket id); `None` when all suites' L3 handoffs exist. Uses ticket/thread fixtures (no real
  agents).
- Unit/seam tests for each spawn helper: ensures the correct ticket id + loads the correct `po-l*` role
  (mock the agent runner, assert role + ticket).
- `uv run pytest tests/ -q -k "po_l or next_incomplete or level"`; lint/format.

### 3. Activate the medium branch (classify + states + run loop + sa_role flip)

**What:**
- Add `PO_L0_CONVERSATION`/`PO_L1_CONVERSATION`/`PO_L2_CONVERSATION`/`PO_L3_CONVERSATION` to `ResumeState`.
- `classify_resume`: insert the medium branch immediately after the `DirState` short-circuits and before
  the `brief` lookup — `if cfg.profile.name == "medium":` delegate to `next_incomplete_level` (step 2) to
  return the matching `PO_L*` state, or `SA_CONVERSATION` when all levels done. Non-medium falls through
  unchanged.
- `run_init` dispatch loop: arms for the four `PO_L*` states calling the step-2 helpers. `classify_resume`
  stays `-> ResumeState` (signature-compatible); the **L3 arm re-runs `next_incomplete_level`** to get the
  pending `(level, suite_id)`, asserts the level is still L3, and passes `suite_id` to
  `run_po_l3_conversation`. Then re-classify (existing loop pattern → auto-cascade).
- SA spawn arm: before spawning `sa_mvp`, assert the required upstream artifacts exist
  (`discovery.md`, `suites.yaml`, each suite's `spec.structured.yaml`); raise a clear error if not
  (fail-loud, design §7).
- Flip `medium.yaml`: `sa_role: sa → sa_mvp`; replace the deferred-note comment with "v2 init pipeline
  active."
- **Known bounded gap**: after this step, the auto path uses thread markers while `/init --proceed` still
  uses disk checks (step 4 rebases it). `develop` is shippable (medium auto-init works end-to-end); the
  manual `/init --proceed` escape hatch may briefly disagree on the L3 step until step 4. Closed in step 4.

**Why:** The activation — a medium `jig init` now runs L0→L1→L2→(L3×suites)→`sa_mvp` end to end.

**Verify:**
- **Resume test (the load-bearing one)**: for a medium project, drive each level's finalize handler, and
  after each assert `classify_resume` returns the next correct state — asserting the exact (ticket id,
  handoff phase) at each boundary (`project`/`po-l1` → L1, … `suite-<id>`/`sa` → SA). Stop-and-resume at
  each level. Include the all-levels-done case → assert `next_incomplete_level` returns `None` and
  `classify_resume` returns `SA_CONVERSATION` (the `None`→SA contract — a mismatch here silently skips SA).
- **Dispatch-loop cascade seam test**: drive the `run_init` loop with mocked spawn helpers (no live agents)
  and assert it visits the four `PO_L*` arms in order (L0→L1→L2→L3) then `SA_CONVERSATION` without operator
  re-entry — covers auto-cascade ordering cheaply (step 5's live scenario is then a confirmation, not the
  sole proof).
- Non-medium regression: `classify_resume` for a `small` project returns the v1 states (unchanged).
- SA fail-loud: a medium project missing `suites.yaml` raises before the SA spawn.
- `uv run pytest tests/ -q -k "classify_resume or resume or init_workflow"`; lint/format.

### 4. Rebase `/init --proceed` onto the shared resolver

**What:** Refactor `_proceed` (`tui/commands/init.py`) to use `next_incomplete_level` (step 2) instead of
its own artifact-on-disk checks, so the manual stepping command and the auto path share one
thread-marker detection convention (design §6). `_proceed` keeps creating/reopening the level ticket the
resolver names.

**Why:** Single source of truth for "which level is next" — the manual escape hatch can't drift from the
auto path on the L3 step.

**Verify:**
- Existing `_proceed` tests pass against the refactor.
- **Divergent-case test (load-bearing — the point of the rebase)**: a suite whose
  `.jig/spec/suites/<id>/brief.md` exists on disk but has **no** `Handoff(phase="sa")` on its `suite-<id>`
  ticket (the exact disk-vs-marker skew). Assert the rebased `_proceed` and the medium `classify_resume`
  resolve the **same** next suite/level — i.e. both still treat that suite as incomplete (marker basis),
  rather than `_proceed` skipping it on disk presence. Also cover the inverse (handoff present, brief.md
  absent due to a partial write).
- A happy-path test that `_proceed` and the medium `classify_resume` agree on the next level for a clean
  project state.
- `uv run pytest tests/ -q -k "proceed or tui_init"`; lint/format.

### 5. End-to-end medium auto-init scenario + full verification

**What:** Add a bones-style scenario (`tests/scenarios/medium-l0-l3-auto-init.scenario.yaml`) that runs a
medium `jig init` and lets the agents auto-cascade L0→L1→L2→L3→`sa_mvp` (no manual `/init --proceed`,
no hand-written `suites.yaml`/architecture), asserting each artifact lands (`brief.md`, `discovery.md`,
`suites.yaml`, per-suite `spec.structured.yaml`, per-module `contracts.yaml`).

**Why:** Confirms the components compose into the behavior the problem requires — and exercises `sa_mvp`
from a live init for the first time.

**Verify:**
- The new scenario passes.
- `uv run ruff check jig/ tests/` and `uv run ruff format --check jig/`; `uv run pytest tests/ -q` — full
  suite green.

## Rollback

Steps 1–2 and 4 are additive or behavior-preserving (up-front selection mirrors `--profile`; helpers are
unwired; the `_proceed` refactor preserves behavior). Step 3 is the activation: reverting its commit
(the `medium.yaml` flip + `classify_resume` branch together) returns medium to the v1 flat path — the
branch is a single guarded `if cfg.profile.name == "medium"`, so non-medium is never affected. The
`sa_role` flip and the classify branch must revert together (the flip alone would route a flat SA after
L0–L3; the branch alone would route L0–L3 then a flat SA).

## Out of scope for this plan

- Building/changing the L0–L3 roles, `*_mcp` handlers, or `sa_mvp` (fail-loud check is in the SA spawn arm).
- The `small` PO+SA mechanics / v1 flat path (beyond PM-1 retirement).
- Option C (unify the brief step on `po-l0` for all sizes).
- A declarative init-pipeline engine.

## Progress

- [x] Step 1: Up-front size selection + PM-1 retirement
- [x] Step 2: L0–L3 spawn helpers + shared level resolver
- [x] Step 3: Activate the medium branch (classify + states + run loop + `sa_role` flip)
- [x] Step 4: Rebase `/init --proceed` onto the shared resolver
- [x] Step 5: End-to-end medium auto-init scenario + full verification

## Change log

- 2026-06-09: Initial draft (brent-hoover)
- 2026-06-09: Revised ×1 (plan-reviewer) — step 4: add the load-bearing disk-vs-thread-marker divergent-case
  verify (suite with brief.md on disk but no `Handoff(phase="sa")`); step 1: assert the *interactive*
  up-front path (not just `--profile`) skips PM-1; step 3: add a dispatch-loop cascade seam test + the
  `None`→`SA_CONVERSATION` contract assertion, and note the `_proceed` disk/marker drift is a known bounded
  gap closed in step 4.
- 2026-06-09: Step 5 realization note — the sim `.scenario.yaml` harness hand-drives each finalize step and
  has no step kind that drives `run_init`'s auto-cascade, so the medium auto-init e2e is implemented as a
  Python test (`tests/test_init_medium_e2e.py`) that drives the real `run_init` dispatch loop with fake
  agents invoking the real L0–L3 + `sa_mvp` finalize handlers. This exercises the orchestration this feature
  added (classify_resume branch → dispatch arms → spawn helpers → finalize → artifacts) end-to-end, which a
  scenario file (explicit `invoke_*` steps) would bypass. Asserts the full artifact chain lands with no
  manual `/init --proceed`.
