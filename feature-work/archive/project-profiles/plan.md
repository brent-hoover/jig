---
title: Project Profiles — Implementation Plan
type: plan
status: active
owner: brent
created: 2026-05-21
updated: 2026-05-21
design: ./design.md
---

# Project Profiles — Implementation Plan

## Overview

Implement project profiles with an agent-driven selection step between the brief and the SA pass.
The flow becomes `PO (brief) → PM-1 (profile, gated by operator) → SA (with cfg.profile.sa_role) →
PM-2 (planning)`. The same PM role handles both passes — it branches on ticket id at the top of its
prompt. The operator confirms PM-1's choice the same way they confirm SA's template proposal:
Y / swap (toggle to the other profile) / n. The `--profile` flag stays as an eval/auto bypass.

## Preconditions

- [x] v1 SA path alignment merged (PR #71).
- [x] Problem + design approved.
- [x] Three open design questions resolved on 2026-05-21:
  - **One PM role**, with mode detection on ticket id.
  - **New `profile` ticket** with its own work type.
  - **Operator gate** mirroring SA template confirm (Y/swap/n).

## Steps

### 1. Already-coded foundation (keep)

These landed during the earlier exploration. None need rework; they're the scaffolding the new pass
hangs on:

- `jig/defaults/workflows/feature-s-full.yaml` — full reviewer federation at `s` size.
- `jig/config.py` — `ProfileSection` on `Config` (backwards-compatible defaults).
- `jig/schemas/profile.py` — Profile + ProfileWorkflows pydantic models.
- `jig/defaults/profiles/{small,medium}.yaml` — the two shipping profiles.
- `jig/profile_loader.py` — `load_profile`, `apply_profile`, `copy_profile_templates`,
  `list_profiles`.
- `jig/cli.py` — `--profile` flag with `_apply_profile_at_start` helper.
- `jig/init_workflow.py` — `_resolve_sa_role` helper + wiring in `run_sa_conversation`.
- `pyproject.toml` — `jig.defaults.profiles = ["*.yaml"]` package-data.
- `tests/test_profile_loader.py` — 11 unit tests (still valid).
- `tests/test_profile_e2e.py` — 6 e2e tests (still valid; cover the `--profile` flag path).
- `jig/defaults/roles/pm.yaml` — the **contract-impact sizing** block (keep; applies to PM-2).

### 2. Revert the recommendation-only prose

**What:** Remove the "Process 0" block I added to `jig/defaults/roles/pm.yaml`. The mode-detection
block (step 9) replaces it.

**Why:** That block told the PM to recommend a profile but stopped short of acting — the user
explicitly rejected the recommend-and-let-operator-type-a-command model. Removing avoids prompt
contradiction with the new mode-detection block.

**Verify:** `grep -n "jig start --profile" jig/defaults/roles/pm.yaml` returns no matches.

### 3. Add `WorkType.PROFILE`

**What:**

- `jig/ticket.py:WorkType` — add `PROFILE = "profile"` value.
- Confirm `_WORK_TYPES_REQUIRING_AC` does NOT include `"profile"` (it's an init ticket, like
  `brief`/`architecture`/`planning`).

**Why:** Init tickets get their own work type so the orchestrator + tests can recognise them
without string-sniffing the title.

**Verify:** `Ticket(id="profile", work_type=WorkType.PROFILE, title="Pick project profile",
created_by="cli")` constructs cleanly without an AC requirement.

### 4. Add `ResumeState` values + `classify_resume` detection

**What:** `jig/init_workflow.py`:

```python
class ResumeState(str, Enum):
    ...
    PM_PROFILE_PASS = "pm_profile_pass"
    PM_PROFILE_CONFIRM_PROMPT = "pm_profile_confirm_prompt"
    ...
```

In `classify_resume`: after `SPEC_GENERATION` finishes (or, in the `--brief` path, after the spec
is in place), the next state is gated on `cfg.profile.name`:

- If `cfg.profile.name` is set (operator passed `--profile` OR a previous profile pass already
  resolved) → fall through to `SA_CONVERSATION` (current behaviour).
- Else if no `pm_propose_profile` Note exists on the profile ticket → `PM_PROFILE_PASS`.
- Else if a proposal exists but operator hasn't confirmed → `PM_PROFILE_CONFIRM_PROMPT`.

**Why:** The state machine is the natural seam — it already classifies which phase to run next and
gates on prior artifacts.

**Verify:** Unit test in `tests/test_init_workflow.py` (or `_resume_state` if isolated): seed a
fresh project with brief + spec but no profile → assert classify returns `PM_PROFILE_PASS`.

### 5. Add `pm_propose_profile` MCP tool

**What:** New handler in `jig/init_mcp.py` (or its profile-specific cousin):

```python
async def handle_pm_propose_profile(
    *,
    tickets, threads, bus, project_path, name, rationale, author,
) -> str:
    """Record the PM's profile choice as a thread Note + resolve the
    profile ticket. The init resume loop picks up the proposal via
    latest_profile_proposal() on the next pass."""
```

The Note carries `kind="pm_propose_profile"` payload with `name` and `rationale`. Mirrors
`handle_sa_propose_scaffold` exactly.

**Why:** The PM agent needs a single end-of-run primitive — propose-and-end. The workflow does the
config mutation after the operator confirms; the agent just records its choice.

**Verify:** Unit test: call the handler, assert a Note with the expected payload exists on the
profile ticket and the ticket is RESOLVED.

### 6. Create the profile ticket in `run_init`

**What:** Add a `_create_profile_ticket(tickets)` helper that creates the profile ticket once,
called after brief approval and before SA_CONVERSATION dispatch. Idempotent — no-op if the ticket
already exists.

**Why:** The PM needs a ticket to run against. Init tickets are created at init time, not at
dispatch time.

**Verify:** Test: after `run_init` completes the brief-approval state, `tickets.get("profile")`
returns a non-None Ticket with `work_type == WorkType.PROFILE`.

### 7. Profile-confirm UX helpers

**What:** In `jig/init_workflow.py`, mirroring the SA confirm pattern:

- `latest_profile_proposal(threads)` — returns the most-recent `pm_propose_profile` Note payload
  on the profile ticket, or `None`.
- `render_profile_confirm_prompt(name, rationale)` — short Rich-formatted string for scrollback.
- `prompt_profile_confirm(threads, console, prompts)` — returns `(ConfirmChoice, dict)`.
- `PromptHandler.ask_profile_confirm(name, rationale, console)` method on the prompt-handler
  interface (with CLI + Auto + Test implementations in `init_prompts.py`).

**Why:** Reusing the SA confirm shape means the operator gets a consistent gate UX and the auto
mode (eval scenarios) follows the same patterns.

**Verify:** Tests for each helper; round-trip the proposal-Y / proposal-swap / proposal-N branches
against the in-memory thread store.

### 8. Resume-loop dispatch for the two new states

**What:** In `_run_init_resume_loop`:

```python
if rs == ResumeState.PM_PROFILE_PASS:
    # Spawn PM against the profile ticket. Mode detection in the
    # PM prompt branches it into profile-pick mode.
    await run_pm_profile_pass(
        project_path=target, tickets=tickets, threads=threads,
        memory=memory, bus=bus, console=console,
    )
    continue
if rs == ResumeState.PM_PROFILE_CONFIRM_PROMPT:
    choice, proposal = await prompt_profile_confirm(threads, console=console, prompts=prompts)
    if choice == ConfirmChoice.YES:
        # Workflow-side apply (not the agent's job).
        profile = load_profile(proposal["name"], project_path=target)
        cfg = apply_profile(load_config(target), profile)
        save_config(target, cfg)
        copy_profile_templates(profile, target)
    elif choice == ConfirmChoice.SWAP:
        # Toggle to the other profile and apply directly.
        other = "small" if proposal["name"] == "medium" else "medium"
        profile = load_profile(other, project_path=target)
        cfg = apply_profile(load_config(target), profile)
        save_config(target, cfg)
        copy_profile_templates(profile, target)
    else:
        console.print("Profile not approved. State saved.")
        return
    continue
```

**Why:** Each resume tick handles one transition; the SA template confirm pattern is the precedent.

**Verify:** Step 11's e2e test exercises this loop.

### 9. PM prompt mode detection

**What:** At the very top of `jig/defaults/roles/pm.yaml`, before the existing "Process":

```yaml
## Mode

  Check your ticket id first.

  - If it's ``profile``: you're in **profile-selection mode**. Read
    ``docs/brief.md`` and ``.jig/spec/project.structured.yaml``. Assess
    project complexity using signals like external integrations, multiple
    datastores, uncommon protocols, realtime requirements, auth/PII,
    compliance, HA SLOs, multi-writer state, background jobs, public
    versioned APIs, event-driven architecture, plugins. Use holistic
    judgment — no numerical thresholds. When in doubt, prefer the
    heavier profile. Choose ONE of: ``small`` or ``medium``. Call
    ``pm_propose_profile(name=<small|medium>, rationale=<short
    explanation referencing the signals you saw>)`` exactly once. Do
    NOT create any tickets, do NOT use ``ask_question``, do NOT read
    architecture artifacts (SA hasn't run yet). End your run.

  - If it's ``planning`` (or anything else): you're in **planning
    mode**. Follow the process below.
```

The existing `pm_propose_profile` tool needs to be added to `pm.yaml`'s `allowed_tools` list.

**Why:** One role file; the branching keeps the agent context lean for the small profile-pick task.

**Verify:** Step 11's e2e test asserts that the PM-1 run produces a `pm_propose_profile` Note and
does not produce planning tickets.

### 10. `--profile` flag preserves the bypass

**What:** Confirm (no code change needed) that when `--profile` was passed at start, `cfg.profile.name`
is non-empty and `classify_resume` falls through to `SA_CONVERSATION` without going through
`PM_PROFILE_PASS`. The `_apply_profile_at_start` helper already writes the profile name into
config.

**Why:** Eval/auto mode needs to skip the PM-1 pass deterministically. The existing flag path does
that for free as long as the classify_resume logic checks `cfg.profile.name` first.

**Verify:** A test in `tests/test_profile_e2e.py` confirms that after `_apply_profile_at_start`,
`classify_resume` returns `SA_CONVERSATION` (not `PM_PROFILE_PASS`).

### 11. End-to-end test for the full new flow

**What:** `tests/test_init_workflow_e2e.py` new test:

```python
async def test_profile_pm_pass_full_flow(...):
    # 1. Fresh project; PO + spec already seeded (use the existing
    #    seeded-brief helper).
    # 2. Drive run_init forward; assert it stops at PM_PROFILE_PASS.
    # 3. Inject a fake PM run that calls handle_pm_propose_profile(small).
    # 4. Drive forward; assert PM_PROFILE_CONFIRM_PROMPT.
    # 5. Inject a YES answer via the test PromptHandler.
    # 6. Drive forward; assert cfg.profile.name == "small",
    #    _resolve_sa_role returns "sa", workflows copied into .jig/.
    # 7. Drive forward; assert next state is SA_CONVERSATION.
```

A second test exercises the SWAP branch from a small proposal to medium.

**Why:** This is the integration confidence test for the full new path. Mocks the agent run (no
real Claude call) but exercises the entire init state machine + MCP handler + workflow helpers.

**Verify:** Test passes; one assertion per numbered step.

### 12. Commit + PR

**What:** Single commit on `feat/project-profiles`. Conventional commit subject:

```
feat(init): Add project profiles with PM-driven selection
```

Body:
- The wiring-mismatch + run-38efa7d7 analyzer recommendations that motivated profiles.
- The PO → PM-1 → SA → PM-2 flow with operator gate at PM-1.
- File-by-file change summary.
- `--profile` flag preserved for auto/eval mode.

**Verify:** PR opens against develop with full Problem/Fix, manual test steps for both the
PM-driven and `--profile` paths, and the checklist.

## Rollback

Single commit, fully revertable. `git revert` undoes everything atomically. No data migration —
existing v1 projects without a profile field continue to work via the empty-string defaults.

## Out of scope for this plan

- Mid-project profile switching (depends on the project-onboarding upscale sequence; separate
  feature).
- Per-ticket profile overrides.
- A `large` profile (design explicitly defers).
- Auto-detection of profile from brief signals (the PM-1 pass IS the auto-detection — no separate
  heuristic engine).
- Adaptive process calibration (reviewer signal → automatic profile adjustment).
- Profile versioning / migration tooling.
- Consolidating `sa.yaml` into `sa_mvp` (explicitly decided against on 2026-05-21).

## Change log

- 2026-05-21: Initial draft (brent)
- 2026-05-21: Rewritten after user feedback. Original CLI-flag-or-PM-needs_info design replaced
  by PO → PM-1 (gated by operator) → SA → PM-2 flow. PM-1 picks the profile from the brief so the
  operator isn't asked to judge project size without context. The operator gates by confirming
  PM-1's choice (Y/swap/n), mirroring the SA template confirm.
