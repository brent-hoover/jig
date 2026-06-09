---
title: Project Onboarding — Implementation Plan
type: plan
status: active
owner: brent-hoover
created: 2026-06-09
updated: 2026-06-09
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
- [ ] (Steps 7–9) sa-architect Phase 2 merged — unified SA role `role` id and `allowed_tools` list
      confirmed and frozen
- [ ] (Steps 7–9) Sandbox egress policy for onboard-phase agents confirmed (shared open question with
      sa-architect)

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

### 7–9. SA read pass, operator review, PM backlog (BLOCKED)

**BLOCKED**: These steps require sa-architect Phase 2 (unified SA role id and `allowed_tools`) and
sandbox egress policy confirmation. Do not implement until those dependencies resolve.

Once sa-architect Phase 2 ships:
- **Step 7**: SA read pass — `run_onboard_sa_conversation()` with per-module M-path tools,
  `sa_write_boundaries`, depth-budget injection, `n_a_categories` instruction in prompt.
- **Step 8**: Operator review gate — `ask_onboard_review` PromptHandler method, artifact summary
  render, `onboard_artifacts_approved` event.
- **Step 9**: PM backlog bootstrap — PM spawned with `brief.md` + `desired-state.md` +
  `observations.md` context; `onboard_completed_at` written on completion; ALREADY_DONE detection.

This plan will be updated with step detail when the blocking prerequisites resolve.

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
