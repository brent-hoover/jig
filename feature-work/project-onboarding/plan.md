---
title: Project Onboarding — Implementation Plan
type: plan
status: superseded
superseded_by: ../../architecture/plan.md
owner: brent-hoover
created: 2026-06-09
updated: 2026-06-26
design: ./design.md
---

# Project Onboarding — Implementation Plan

## Overview

We build `jig onboard` in two phases. Phase 1 (steps 1–6) covers everything up to and including the
scanner pass, PO read pass, and PM profile selection — all of which are independent of sa-architect.
Phase 2 (steps 7–9) adds the SA read pass, operator review gate, and PM backlog bootstrap; these are
blocked until sa-architect Phase 2 ships and freezes the unified SA role interface. Steps 1–6 can be
developed and tested end-to-end now. Steps 7–9 are left as stubs in this plan and will be filled in
once the sa-architect blocking prerequisite resolves. The implementation order is strictly top-down
through the state machine: each step unblocks the next state transition.

## Preconditions

- [x] Design approved
- [x] (Steps 7–9) sa-architect Phase 2 merged — unified SA role `role` id and `allowed_tools` list
      confirmed and frozen (PR #154, merged 2026-06-10)
- [x] (Steps 7–9) Sandbox egress policy for onboard-phase agents confirmed (shared open question with
      sa-architect — resolved: same open egress policy as init flow)

## Steps

### 1. Foundation: WorkType, MCP tool, scanner role

**What:** Add `ONBOARD_SCAN = "onboard_scan"` to `WorkType` in `jig/ticket.py` (alongside existing
values). Add `handle_onboard_finish_scan` to `jig/init_mcp.py` (parallel to
`handle_po_finish_brief`): writes `Note(text="scan complete", payload={"kind": "onboard_scan_done"})`
to the `onboard-scan` ticket thread and resolves the ticket. (`Note.kind` is a fixed discriminator
`"note"` — the scan marker lives in `payload`, not `kind`.) Register it in `jig/mcp_server.py`'s
tool dispatch, scoped to the `scanner` role. Add `jig/defaults/roles/scanner.yaml` with the role
definition from the design (Read, Glob, Grep, Bash read-only, Write prompt-scoped to
`observations.md` and `CLAUDE.md`, ToolSearch, no MCPs). Also add `"onboard_artifacts_approved"`
to the `SystemEvent.event_type` Literal in `jig/thread.py` (needed by the operator review gate in
step 8; adding it here keeps the model change alongside the other onboard additions).

**Why:** All downstream scanner-pass code depends on `WorkType.ONBOARD_SCAN` and
`onboard_finish_scan` existing. The role file must exist before any agent spawn attempt.

**Verify:** `from jig.ticket import WorkType; WorkType.ONBOARD_SCAN` resolves without error;
`handle_onboard_finish_scan` importable and registered; `scanner.yaml` loads via
`load_role("scanner")` without error; unit test that `handle_onboard_finish_scan` writes a `Note`
with `payload={"kind": "onboard_scan_done"}` and resolves the ticket.

---

### 2. State machine skeleton and project.yaml initialization

**What:** Create `jig/onboard_workflow.py`. Add `OnboardResumeState` enum (all values from design).
Add `classify_onboard_resume(project_path, tickets, threads) -> OnboardResumeState` — full
implementation of all state transitions up through `PM_PROFILE_CONFIRM_PROMPT`; states
`SA_READ_PASS` through `PM_BACKLOG` raise `NotImplementedError` for now. Add `run_onboard(path,
brief_file, force, profile)` with this initialization order: (1) run preflight classification via
`classify_directory` — reject with an error if `.jig/` already contains a completed or
non-onboard project, unless `--force` is set; (2) if `--force`, execute the snapshot/rmtree/restore
sequence first: snapshot `.jig/profiles/*.yaml` and `.jig/workflows/*.yaml` (operator-authored,
inside `.jig/`), `rmtree(.jig/)`, then proceed; (3) call `create_stub(path, name=path.name)` and
write `onboard_started_at` into `project.yaml`; (4) create `.jig/onboard/`; (5) if `brief_file`
provided, copy it to `.jig/onboard/desired-state.md`; (6) restore snapshotted profile/workflow
YAMLs.

**Why:** `classify_onboard_resume` drives the loop. Writing project.yaml via `create_stub` first
ensures `classify_directory` returns `IN_PROGRESS` on crash-resume. The `--force` reset must be
idempotent before any agents run.

**Verify:** Unit tests for `classify_onboard_resume` covering: fresh dir → `SCAN_PASS`; after
scan-done note → `PO_READ_PASS`; after PO handoff but no `brief_approved` → `PO_REVIEW`; after
`brief_approved` → `SPEC_PASS`; after profile confirmed → `SA_READ_PASS` (raises
`NotImplementedError`). Test that `run_onboard` with `--force` clears `.jig/` and recreates the stub AND that
`.jig/profiles/*.yaml` and `.jig/workflows/*.yaml` are snapshotted before `rmtree` and restored
after `create_stub`. Test
that `desired-state.md` lands at `.jig/onboard/desired-state.md` when `--brief` is provided.

---

### 3. CLI command

**What:** Add `jig onboard` to `jig/cli.py` as a Click command with options `--brief FILE`,
`--force`, `--profile NAME`. Validate that `path` is a git repository (non-git is unsupported per
design). `path` defaults to `.`. CLI passes `--brief` as a path to `run_onboard()`; the file copy
to `.jig/onboard/desired-state.md` is `run_onboard`'s responsibility (Step 2).

**Why:** Entry point needed for manual testing of all subsequent steps.

**Verify:** `jig onboard --help` renders correct option descriptions. `jig onboard /nonexistent`
exits with a clear error. `jig onboard` (no args) uses current directory. Passes `--brief`,
`--force`, `--profile` through to `run_onboard`.

---

### 4. Scanner pass

**What:** Implement `run_onboard_scan_pass(project_path, tickets, threads, agents)` in
`onboard_workflow.py`: creates the `onboard-scan` ticket (`WorkType.ONBOARD_SCAN`), injects the
depth-budget ceiling from the active profile into the ticket description (150 files for `small`,
400 for `medium`/unknown), spawns the scanner role. Add `SCAN_PASS` dispatch to the `run_onboard`
while loop. `classify_onboard_resume` SCAN_PASS detection: no `onboard-scan` ticket → `SCAN_PASS`;
`Note` with `payload.get("kind") == "onboard_scan_done"` on `onboard-scan` → `PO_READ_PASS`.

**Why:** The scanner produces `observations.md`, which PO and SA both read as shared context. All
subsequent passes depend on it.

**Verify:** Integration test (or manual run against a small test repo): `jig onboard .` creates
`onboard-scan` ticket, scanner agent runs, `observations.md` appears at `.jig/onboard/observations.md`,
ticket resolves with scan-done note. Re-running after scan completes resumes at `PO_READ_PASS`
without re-scanning.

---

### 5. PO read pass and PO review gate

**What:** Implement `run_onboard_po_conversation(project_path, tickets, threads, agents)`: creates
the standard `brief` ticket with an injected description stating the agent is in read mode (extract
existing capabilities from the codebase and `observations.md`; do not invent capabilities not yet
built). PO tools (`brief_set_section`, `po_finish_brief`) run unchanged. Add `PO_REVIEW` gate: reuse `ask_brief_approval` from `init_prompts.py` — it already posts
`SystemEvent(event_type="brief_approved")` and supports a Resume-PO option; no new `PromptHandler`
method is needed. Wire `run_spec_generator` for `SPEC_PASS` (reused unchanged).

**Why:** `run_spec_generator` hard-requires `tickets.get("brief")` and reads `docs/brief.md`.
The PO_REVIEW gate provides the operator confirmation required by the problem statement before
spec generation runs.

**Verify:** After scanner completes: `jig onboard .` spawns PO on `brief` ticket with read-mode
context; `docs/brief.md` is written; loop pauses at PO_REVIEW prompt; operator confirms; `brief_approved`
event is posted; `run_spec_generator` runs and produces specs. Re-running after `brief_approved`
resumes at `SPEC_PASS` without re-running PO.

---

### 6. PM profile pass and profile confirm

**What:** Implement `run_onboard_pm_profile_pass(project_path, tickets, threads, agents)`: reads
the scanner's profile recommendation from `observations.md` and pre-injects it into the profile
ticket description before spawning the PM (PM has no Read/Glob/Grep tools — signal must arrive
pre-injected). Wire `run_pm_profile_pass` and `prompt_profile_confirm` (reused from
`init_workflow.py`). If `--profile NAME` was passed, skip `PM_PROFILE_PASS` and
`PM_PROFILE_CONFIRM_PROMPT` entirely: write the named profile to config using
`apply_profile`/`save_config`/`copy_profile_templates` (same pattern as `init_workflow.py`'s
profile-apply block). `classify_onboard_resume` detects the profile-bypassed state by reading
`cfg.profile.name` from config — not from a CLI flag (flags are absent on resume). Classify
`PM_PROFILE_PASS` and `PM_PROFILE_CONFIRM_PROMPT` states in `classify_onboard_resume`.

**Why:** Profile selection completes Phase 1. After profile is confirmed, `load_config().profile`
is available for the SA depth-budget lookup (Phase 2). The `--profile` bypass lets operators skip
the PM conversation when the profile is already known.

**Verify:** After spec pass: PM receives profile ticket with scanner recommendation in description;
operator confirms profile; `load_config(project_path).profile.name` returns the selected profile.
With `--profile small`: PM profile pass is skipped; `small` profile is applied directly. Phase 1
complete: `classify_onboard_resume` returns `SA_READ_PASS` (raises `NotImplementedError`).

---

### 7. SA read pass

**What:** Implement `run_onboard_sa_conversation(project_path, tickets, threads, memory, bus,
console)` in `onboard_workflow.py`. Creates the standard `architecture` ticket if absent (id
`"architecture"`, `WorkType.ARCHITECTURE`), with a description stating read mode: extract existing
modules, contracts, and boundaries from the codebase and `observations.md`; do not design new
architecture; call `arch_finalize` with `n_a_categories` on any module whose contracts could not
be fully discovered within the turn budget. Injects the turn budget (25 turns for `small`, 60 for
`medium`/unknown) from `load_config(project_path).profile.name`. Always spawns `sa_mvp` via
`load_role(project_path, "sa_mvp")` — bypasses `_resolve_sa_role()`, because the read pass always
needs the module-producing SA regardless of profile. Reactivates the architecture ticket if already
resolved (mirrors `run_sa_conversation`). Adds `SA_READ_PASS` and `NEEDS_ANSWER_ARCH` dispatch to
`_run_onboard_resume_loop`. `classify_onboard_resume` SA_READ_PASS detection: profile set but no
`architecture` ticket → `SA_READ_PASS`; `Question` entries on `architecture` with no `Answer` →
`NEEDS_ANSWER_ARCH`; `Handoff(phase="pm")` on `architecture` (SA done) → `OPERATOR_REVIEW`.

**Why:** SA produces `architecture.yaml` and per-module `contracts.yaml` — the primary artifacts
the operator reviews. `sa_mvp` always runs here because it has `sa_write_boundaries` and the full
`arch_set_*` tool set; the v1 `sa` role lacks these and cannot do a read pass. Depth budget is
injected into the description because the SA has no turn-counting capability.

**Verify:** After profile selection: `jig onboard .` creates `architecture` ticket with read-mode
description including turn budget; SA runs; `.jig/spec/architecture.yaml` and at least one
`contracts.yaml` written; `Handoff(phase="pm")` posted. Re-running resumes at `OPERATOR_REVIEW`
without re-spawning SA. With unanswered questions: loop pauses at `NEEDS_ANSWER_ARCH`.

---

### 8. Operator review gate

**What:** Add `ask_onboard_review(*, artifacts, console) -> str` to `PromptHandler` protocol and
`CliPromptHandler` / `AutoPromptHandler` (auto-confirms). `artifacts` is a rendered plain-text
summary string. Implement `render_onboard_review_prompt(project_path) -> str` in
`onboard_workflow.py`: reads `docs/brief.md` headline (first H1), counts spec files under
`.jig/spec/`, lists module ids from `architecture.yaml`, notes whether `desired-state.md` exists.
Implement `prompt_onboard_review(project_path, threads, prompts, console)`: renders the summary,
calls `ask_onboard_review`, on confirm posts
`SystemEvent(event_type="onboard_artifacts_approved")` on the `architecture` thread. Operator
choices: confirm (advance to PM_BACKLOG), re-run SA (force-reactivate architecture ticket, returns
to SA_READ_PASS). `classify_onboard_resume` OPERATOR_REVIEW detection: `Handoff(phase="pm")` on
`architecture` AND no subsequent `onboard_artifacts_approved` event → `OPERATOR_REVIEW`.

**Why:** Operator sign-off is required before the PM generates tickets from the delta — the
artifacts represent what jig will treat as the authoritative current-state baseline for all future
work. Last chance to catch scanner hallucinations or SA misclassifications before they drive tickets.

**Verify:** After SA completes: loop pauses, renders artifact summary; operator confirms;
`onboard_artifacts_approved` posted; loop advances to PM_BACKLOG. On re-run: architecture ticket
reactivated, SA re-spawned. `AutoPromptHandler` auto-confirms.

---

### 9. PM backlog bootstrap and completion

**What:** Implement `run_onboard_pm_backlog(project_path, tickets, threads, memory, bus, console)`
in `onboard_workflow.py`. Creates a `backlog` ticket (`WorkType.PLAN`, id `"backlog"`) with a
description that includes: (a) the delta framing between `docs/brief.md` (current state) and
`.jig/onboard/desired-state.md` (target, if present), (b) the module list from `architecture.yaml`,
(c) a pointer to the specs. Spawns the `pm` role. On `Handoff` from PM, writes
`onboard_completed_at` (ISO timestamp) into `project.yaml` and prints a completion message. If no
`desired-state.md` exists, skip the PM spawn and write `onboard_completed_at` directly — backlog
generation requires a delta to work from. `classify_onboard_resume` PM_BACKLOG detection:
`onboard_artifacts_approved` event posted AND no `onboard_completed_at` in `project.yaml` →
`PM_BACKLOG`. ALREADY_DONE detection: `onboard_completed_at` present → `ALREADY_DONE`.

**Why:** The backlog bootstrap converts the delta between current state and desired state into an
actionable ticket set. `onboard_completed_at` is the canonical completion signal (mirrors
`template_applied_at` in greenfield).

**Verify:** After operator review: PM spawned with current-state brief + desired-state delta +
module context; Handoff from PM; `project.yaml` gains `onboard_completed_at`; re-run exits with
"already onboarded" message. Without `desired-state.md`: PM skipped, completion written directly.

## Rollback

`jig onboard` does not modify any existing source files. All state lives under `.jig/`. If something
goes wrong mid-onboard, `rm -rf .jig/` returns the repository to pre-onboard state. `CLAUDE.md`
is the only root-level file the scanner can write — and only when none existed before. If one existed
already the scanner never touches it. If the scanner wrote a new one, rollback is `rm CLAUDE.md`
(there is nothing in git to restore).

## Out of scope for this plan

- Test-adequacy review pass (deferred to Optimal path)
- Incremental re-onboard preserving existing tickets
- Adaptive (real-time token-aware) depth bounding
- Automatic contract extraction from OpenAPI/protobuf files

## Change log

- 2026-06-09: Initial draft (brent-hoover)
- 2026-06-09: Steps 1–6 implemented on `feat/project-onboarding`. Two additions beyond the design's state
  list, both within existing greenfield vocabulary: (1) a `NEEDS_ANSWER_BRIEF` state mirroring the
  greenfield flow — the PO keeps `ask_question`, and without the state an open Question would respawn-loop
  the PO (the agent loop treats NEEDS_INFO as terminal); (2) fresh `spec_gaps_reported` after approval
  routes back to `PO_REVIEW` so the operator gate decides (edit brief / resume PO / re-approve) instead of
  re-running the spec generator unattended in a loop. Also: the PO read pass embeds `observations.md`
  content into the brief ticket description (the PO role has no Read/Glob/Grep — same pre-injection
  constraint the design states for the PM), and `scanner.yaml` mandates verbatim H2 section headings so
  the PM pass can extract `## Profile recommendation` by name. (brent-hoover)
