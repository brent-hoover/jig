---
title: Project Onboarding — Design
type: design
status: draft
owner: brent-hoover
created: 2026-06-07
updated: 2026-06-09
problem: ./problem.md
---

# Project Onboarding — Design

## Summary

Add a `jig onboard <path>` CLI command that imports an existing codebase into the jig workflow. A new
**scanner** agent runs first and produces a free-form `.jig/onboard/observations.md` — a structural report
covering modules, technology stack, existing tests, and integration surfaces — that both the PO and SA agents
read as shared context. The PO extracts suites from existing behavior into the standard `brief` ticket; the
spec generator runs unchanged. The SA always runs in per-module mode (regardless of profile), extracting
`architecture.yaml`, per-module `contracts.yaml`, and `boundaries.yaml` from existing structure using the
M-path tools. Profile selection uses codebase signals from the scanner rather than brief content; the profile
controls depth budget (file ceiling, turn limit) only — not which SA tool set to use. An operator review gate
fires before any backlog is created. A new `jig/onboard_workflow.py` module implements the state machine,
reusing `classify_directory`, store setup, `run_spec_generator`, and profile-confirm machinery from
`init_workflow.py`.

## Approach

### Scanner agent

A new `scanner.yaml` role runs at the start of the onboard flow. The scanner has Read, Glob, and Grep
access (no Bash — the scanner runs on the host against untrusted third-party repo content, and all tool
constraints are prompt-level only; Glob/Grep/Read cover structural scanning without giving a
prompt-injection payload a shell). It reads the existing codebase and writes `.jig/onboard/observations.md` — a free-form
markdown document covering:

- **Project structure**: top-level packages/modules, directory layout, entry points
- **Technology stack**: language, framework, key dependencies (derived from lock files, pyproject.toml, etc.)
- **Existing tests**: test framework, location, rough coverage estimate, identified gaps
- **External integrations**: APIs, databases, message queues, auth providers
- **Existing contracts**: OpenAPI specs, protobuf definitions, or similar if present
- **Profile recommendation**: `small` or `medium` with brief rationale based on module count,
  integration surface, and test complexity
- **CLAUDE.md status**: whether one exists and its content summary

When done, the scanner calls a new MCP tool `onboard_finish_scan` (parallel to `po_finish_brief`) which
writes a `Note` with `kind: "onboard_scan_done"` to the `onboard-scan` ticket thread and resolves the ticket.

If no `CLAUDE.md` exists at the project root, the scanner writes one based on what it observed before calling
`onboard_finish_scan`. If `CLAUDE.md` already exists, the scanner reads it for context and skips generation.

Scanner `Write` access is prompt-scoped to `.jig/onboard/observations.md` and `CLAUDE.md`. There is no
transport-level enforcement (the onboard spawn runs on the host, where capability hooks don't
materialize), so the scan pass verifies after the agent exits: the high-value surfaces (`.git/config`,
`.git/hooks/**`, `.jig/config.yaml`, `.jig/roles|profiles|workflows/**`, and any pre-existing `CLAUDE.md`)
are hash-snapshotted around the scan, and a `git status` sweep catches other unexpected writes. Any
violation fails the onboard with instructions to inspect before running git hooks or jig agents.
Residual risk (accepted): writes into jig's own runtime state (`.jig/store/`, `.jig/logs/`) are
indistinguishable from the orchestrator's and are not guarded.

### State machine

`jig/onboard_workflow.py` implements `run_onboard()` and `classify_onboard_resume()`. The pattern mirrors
`init_workflow.py`: a while loop that calls `classify_onboard_resume()` each tick and dispatches to the next
step. `classify_onboard_resume()` is a standalone pure function — it does not call `classify_resume()` and
shares no dispatch logic with the greenfield flow.

```
OnboardResumeState (enum):
  SCAN_PASS               → spawn scanner on 'onboard-scan' ticket (WorkType.ONBOARD_SCAN, new);
                            write observations.md; write CLAUDE.md if absent
  PO_READ_PASS            → spawn PO on 'brief' ticket with onboard context + depth budget injected
  PO_REVIEW               → gate: show docs/brief.md to operator for confirmation before spec generation;
                            operator edits brief.md in place and confirms via PromptHandler;
                            confirmation posts SystemEvent(event_type="brief_approved") on the 'brief'
                            ticket — classify_onboard_resume advances to SPEC_PASS only after that event
                            (same durable-event pattern as BRIEF_APPROVAL in the greenfield flow)
  SPEC_PASS               → run_spec_generator (reused unchanged — reads 'brief' ticket)
  PM_PROFILE_PASS         → run_pm_profile_pass with scanner recommendation injected into profile
                            ticket description (PM has no Read/Glob/Grep — signal must be pre-injected)
  PM_PROFILE_CONFIRM_PROMPT → prompt_profile_confirm (reused from init_workflow); operator confirms the
                            PM's profile proposal; on confirm, profile is written to config via
                            apply_profile/save_config; classify_onboard_resume advances when
                            cfg.profile.name is non-empty (same signal as greenfield flow)
  SA_READ_PASS            → spawn per-module SA on 'architecture' ticket with onboard context + depth budget;
                            BLOCKED until sa-architect Phase 2 interface is frozen
  NEEDS_ANSWER_ARCH       → SA posted one or more Question entries on the 'architecture' ticket that have no
                            Answer; operator provides answers via the standard needs_info pause; classify_onboard_resume
                            detects unanswered Questions on the architecture ticket and returns this state;
                            advances when all Questions have corresponding Answers
  OPERATOR_REVIEW         → gate: ask_onboard_review via PromptHandler; operator confirms artifacts
  PM_BACKLOG              → (if .jig/onboard/desired-state.md exists) PM generates initial tickets from delta
  ALREADY_DONE
  BROKEN
```

`classify_onboard_resume()` uses `onboard_completed_at` in `project.yaml` as the ALREADY_DONE signal
(parallel to `template_applied_at` in the greenfield flow; onboarding never sets `template_applied_at`).

**`Handoff(phase="pm")` semantics in the onboard path**: In `classify_resume` (greenfield),
`Handoff(phase="pm")` on the `architecture` ticket maps to `ALREADY_DONE`. In `classify_onboard_resume`,
the same Handoff maps to `OPERATOR_REVIEW` — the SA is done but artifacts have not been operator-approved
yet. Only the subsequent `SystemEvent(event_type="onboard_artifacts_approved")` advances the loop past
`OPERATOR_REVIEW`. This divergence is intentional and safe because the two functions are independent;
neither calls the other.

### Context injection for PO and SA

Rather than new role files, onboarding context is injected into the ticket description before the agent is
spawned.

**PO read pass** — `run_onboard_po_conversation()` creates the standard `brief` ticket with a description
that states the agent is in read mode: extract existing capabilities from the codebase and `observations.md`,
do not invent capabilities not yet built. The PO's MCP tools are entirely unchanged — it still calls
`brief_set_section` and `po_finish_brief`, posting `Handoff(phase="spec-generator")` when done. This lets
`run_spec_generator` (which hard-requires `tickets.get("brief")`) run unmodified in the next state.

**SA read pass** — `run_onboard_sa_conversation()` creates the standard `architecture` ticket with a
description that states the agent is in read mode: extract existing modules, contracts, and boundaries from
the codebase and `observations.md`, do not design new architecture. The SA uses the M-path tool set:
`arch_set_module`, `arch_set_data_store`, `arch_set_shared_contract`, `arch_set_cross_cutting_policy`,
`module_set_owned_collection`, `module_set_external_dependency`, `module_set_behavioral_contract`,
`module_set_data_contract`, `arch_finalize` — all of which already exist in `sa_incremental_mcp.py`. The
only tool that does not yet exist is `sa_write_boundaries`, which the sa-architect feature adds. The unified
SA role that wires this tool set (Phase 2 of sa-architect) also does not yet exist. `run_onboard_sa_conversation()`
bypasses `_resolve_sa_role()` and always spawns this unified Phase-2 SA role regardless of active profile.
The profile controls depth budget only (see **Depth bounding** below).

**`arch_finalize` validation in read mode**: `arch_finalize` runs `validate_module_checklist` which raises
if any module has unmet contract categories and no `n_a_categories` exemption. A budget-truncated read pass
may not fully discover every module's contracts. The SA read-mode prompt must instruct the SA: before calling
`arch_finalize`, set `n_a_categories` on any module whose contracts could not be fully discovered within the
turn budget. This turns validation errors into advisory warnings rather than crashes.

**Interface contract dependency**: `run_onboard_sa_conversation()` needs to know the Phase-2 unified SA
role's `role` id and its `allowed_tools` list. This interface is not yet defined by sa-architect. Planning
for the SA read-pass step is blocked until sa-architect Phase 2 defines and freezes this interface.

### Depth bounding

The problem's Scale complexity driver requires an explicit cap on scanner and SA read-pass depth.

The cap is prompt-level (advisory — included in the ticket description, not technically enforced at the
transport layer) and scales by active profile:

| Profile       | Scanner file ceiling | SA read-pass turn budget |
|---------------|---------------------|--------------------------|
| small         | 150 files            | 25 turns                 |
| medium        | 400 files            | 60 turns                 |
| custom/unknown| 400 files (fallback) | 60 turns (fallback)      |

`run_onboard_sa_conversation()` reads `load_config(project_path).profile.name` to pick the row. Unknown
profile names fall back to `medium` budget. If the `--profile` flag bypasses PM-1 selection, the budget is
derived from the named profile's row.

**Scanner budget note**: `SCAN_PASS` runs before profile selection, so `load_config().profile.name` is
always empty at scan time in a normal invocation. The scanner therefore always uses the 400-file fallback
ceiling unless the operator passes `--profile small` upfront. The 150-file ceiling in the table only applies
in the `--profile small` case. This is intentional: the scanner's job is to gather the signals that drive
profile selection; it cannot yet know which profile will be chosen.

If the scanner hits its file ceiling, it writes a `## Depth limit reached` section in `observations.md`
listing what was not scanned. If the SA hits its turn budget, it calls `arch_finalize` with whatever modules
it has discovered and records incomplete coverage in `architecture.yaml`'s `open_questions` list.

### Operator review gate

Two-phase detection on the `architecture` ticket:

1. **SA complete**: `classify_onboard_resume` detects `Handoff(phase="pm")` (posted by `arch_finalize`) and
   returns `OPERATOR_REVIEW`.
2. **Operator approved**: the CLI calls `ask_onboard_review` on the `PromptHandler` (parallel to
   `ask_force_confirm`; works under both CLI and TUI paths without blocking the daemon). On confirmation,
   `SystemEvent(event_type="onboard_artifacts_approved")` is posted to the `architecture` ticket thread.
   On the next loop tick, `classify_onboard_resume` detects this event and advances.

```
✓ architecture.yaml  — 4 modules
✓ contracts.yaml     — 4 modules
✓ boundaries.yaml    — 4 modules
✓ specs/             — 12 specs

Review the artifacts in .jig/spec/. Press ENTER to continue, or Ctrl-C to edit first.
```

The operator can exit (Ctrl-C), edit files directly, and re-run `jig onboard` to resume. The loop re-enters
`OPERATOR_REVIEW` (the `arch_finalize` Handoff is still there; `onboard_artifacts_approved` is not yet),
renders the same prompt, and waits.

### PM backlog bootstrap

The PO read pass writes `docs/brief.md` via `brief_set_section` on every onboard run — this represents the
**current-state brief** (what already exists). The `run_spec_generator` reads this file; it always exists
after the PO pass and cannot be used as the PM_BACKLOG gate.

The gate is whether the operator supplied a **desired-state brief** via `--brief FILE`. When `--brief FILE`
is passed, the file is copied to `.jig/onboard/desired-state.md` at the start of `run_onboard()` (before any
agents run). `classify_onboard_resume` gates `PM_BACKLOG` on `.jig/onboard/desired-state.md` existing.

Storing the file inside `.jig/onboard/` (rather than `docs/`) means `--force` clears it automatically —
a `--force` re-onboard without `--brief` cannot accidentally trigger PM_BACKLOG from a previous run's
desired-state brief.

When `PM_BACKLOG` fires, the PM is spawned with a ticket description containing:
- `docs/brief.md` (PO's current-state extraction — what is already built)
- `.jig/onboard/desired-state.md` (operator's desired-state brief — what they want next)
- `.jig/onboard/observations.md` (scanner's structural view)

The PM's task: identify capabilities present in `desired-state.md` that are absent or incomplete in
`docs/brief.md` and the produced artifacts, and create tickets for those gaps using its standard
ticket-creation tools.

If no `--brief FILE` was provided, `PM_BACKLOG` is skipped and the flow goes directly to `ALREADY_DONE`.

### Resumability and BROKEN recovery

`project.yaml` is written as the very first action in `run_onboard()` (before any agents are spawned).
This ensures `classify_directory` returns `IN_PROGRESS` (not `BROKEN`) even if the process crashes before
the scanner finishes. On re-run, `classify_onboard_resume` finds the in-progress ticket state and resumes
from the correct step.

If `.jig/` already exists with a valid `project.yaml` from a prior `jig init` or `jig onboard` run, the
command exits with an error unless `--force` is passed. With `--force`, `run_onboard()` reuses the
snapshot/rmtree/restore sequence from `init_workflow.py:203-229`: snapshot operator-authored profile and
workflow YAMLs, `rmtree(.jig/)`, recreate the stub, restore snapshots. Root-level files (`CLAUDE.md`) are
outside `.jig/` and survive `--force` unchanged. `.jig/onboard/desired-state.md` is inside `.jig/` and is
cleared by `--force` — a re-onboard without `--brief` therefore skips PM_BACKLOG, as intended.

## Interfaces

### CLI

```
jig onboard [OPTIONS] <path>

  Import an existing codebase at <path> into the jig workflow.

Options:
  --brief FILE    Path to a desired-state brief describing what to build next.
                  Copied to .jig/onboard/desired-state.md. If omitted, backlog
                  generation is skipped (docs/brief.md is always written by
                  the PO pass and is the current-state brief).
  --force         Clear existing .jig/ and re-onboard from scratch.
  --profile NAME  Skip PM-1 profile selection; apply named profile directly.
```

`path` defaults to `.` (current directory).

### New `WorkType` value

`WorkType.ONBOARD_SCAN` — added alongside `WorkType.BRIEF`, `WorkType.ARCHITECTURE`. Used for the
`onboard-scan` ticket created by `run_onboard_scan_pass()` at the start of `SCAN_PASS`.

### New MCP tool: `onboard_finish_scan`

Registered in `init_mcp.py` alongside `po_finish_brief`. Called by the scanner when `observations.md` is
complete. Must be added to `mcp_server.py`'s tool dispatch table scoped to the `scanner` role.

```python
async def handle_onboard_finish_scan(*, project_path: Path, threads: ThreadStore, tickets: TicketStore) -> str:
    """Write scan-done note and resolve the onboard-scan ticket."""
```

### New PromptHandler method

```python
async def ask_onboard_review(self, *, project_path: Path, console: Console) -> bool:
    """Render artifact summary and gate on operator confirmation."""
```

### New role: `scanner.yaml`

```yaml
role: scanner
phase_prompt: >
  You are a codebase scanner for jig onboard. Read the existing project
  at your worktree root and produce a structural observations document at
  .jig/onboard/observations.md. [full prompt in role file]
allowed_tools:
  - Read
  - Glob
  - Grep
  - Write             # observations.md and CLAUDE.md only (prompt-scoped)
  - ToolSearch
  - onboard_finish_scan  # jig-internal MCP tool; follows the same pattern as
                         # po_finish_brief for PO and sa_propose_scaffold for SA
allowed_mcps: []
```

## Data model

### `.jig/onboard/observations.md`

Free-form markdown produced by the scanner. Not schema-validated — PO and SA read it as prose context.
Written once; overwritten on `--force` re-onboard.

### `docs/brief.md` and `.jig/onboard/desired-state.md`

`docs/brief.md` is written by the PO read pass (via `brief_set_section`) and represents current behavior —
the same path `run_spec_generator` reads. `.jig/onboard/desired-state.md` is written at the start of
`run_onboard()` when `--brief FILE` is passed — it is the operator's desired-state document and is the gate
for `PM_BACKLOG`. Placing it inside `.jig/onboard/` (alongside `observations.md`) means `--force` clears it
with the rest of the onboard state. These two files serve distinct roles and must not be conflated.

### `project.yaml`

`run_onboard()` calls `create_stub(path, name=name)` first — the same function used by the greenfield init
flow. `create_stub` writes `id`, `name`, `created_at`, and initialises `config.yaml`; without these fields,
`classify_directory` returns BROKEN (`{id, name, created_at}` are required, `init_workflow.py` line 81).
Immediately after `create_stub`, `run_onboard()` writes the onboard-specific fields into the same file:

```yaml
# these fields are added by run_onboard() immediately after create_stub()
onboard_started_at: "2026-06-07T21:00:00Z"
# written when onboarding completes
onboard_completed_at: "2026-06-07T22:00:00Z"
```

`classify_onboard_resume` uses `onboard_completed_at` as the ALREADY_DONE signal.
`classify_directory` returns IN_PROGRESS (not BROKEN) on crash-resume because the standard stub fields are
present; the onboard-specific fields are additive.

### Ticket IDs

| Ticket ID      | Role    | Agent-complete signal                          | Advance-past signal                                                          |
|----------------|---------|------------------------------------------------|------------------------------------------------------------------------------|
| `onboard-scan` | scanner | `Note(payload={"kind":"onboard_scan_done"})`   | same                                                                         |
| `brief`        | po      | `Handoff(phase="spec-generator")`              | `SystemEvent(event_type="brief_approved")` (posted by PO_REVIEW gate)        |
| `profile`      | pm      | `Note(payload={"kind":"pm_propose_profile"})`  | `cfg.profile.name` non-empty (written by `apply_profile`/`save_config`)      |
| `architecture` | sa      | `Handoff(phase="pm")`                          | `SystemEvent(event_type="onboard_artifacts_approved")`                       |

`onboard-scan` is the only new ticket ID. `brief` and `architecture` are standard IDs reused from the
greenfield flow; `classify_onboard_resume` treats their signals differently from `classify_resume`.

## Alternatives considered

### Simplest — onboard flag on existing `jig init`

Add `--onboard` to `jig init`. Seed a synthetic brief-approved state and inject a system note into the SA
ticket telling it to read existing code.

*Drawbacks*: The generic `sa` role uses `sa_propose_scaffold` (template-pick flow) — there is no scaffold to
pick against existing code. No scanner or `observations.md`. PO interview skipped entirely — no suite
extraction. No operator review gate before PM. Mode flags scattered through `init_workflow.py` make the
greenfield path harder to reason about.

### Complete — new state machine + scanner + context injection (chosen)

New `onboard_workflow.py` with onboard-specific states, a `scanner.yaml` role, and context injection into
existing PO/SA tickets. SA always uses the per-module M-path tools (bypassing `_resolve_sa_role`). Reuses
`run_spec_generator`, `run_pm_profile_pass`, `prompt_profile_confirm`, and agent-spawning utilities from
`init_workflow.py`. Role files could alternatively be duplicated (`po-onboard.yaml`, `sa-onboard.yaml`), but
context injection achieves the same framing without duplicating maintenance surface.

*Why not Simplest*: The generic SA role has no M-path tool access; read-mode requires the per-module tool
set which only exists after sa-architect Phase 2 lands. Context injection into a dedicated ticket is the
established mechanism for agent framing.

### Optimal — adaptive depth + incremental re-onboard + test-adequacy review

The unconstrained ideal: a cost-aware scanner that dynamically adjusts depth based on real-time token
consumption; an incremental re-onboard path that updates artifacts in place without resetting existing
tickets; a test-adequacy reviewer that runs after the spec pass and produces a gap report; and automatic
contract extraction from OpenAPI/protobuf definitions.

*What we trade away*: depth bounding is advisory (prompt-level, not transport-enforced), re-onboard with
`--force` resets everything, test-adequacy review is not built in. These are deferred, not impossible.

### Decision

**Complete**. Avoids role duplication, reuses proven init machinery for spec generation and profile
selection, and bounds cost via profile-scaled prompt-level limits. The Optimal additions (adaptive depth,
incremental re-onboard, test-adequacy review) are deferred until the basic flow is validated in practice.

## Risks

- **sa-architect Phase 2 prerequisite (hard)**: `jig onboard`'s SA read pass requires sa-architect Phase 2:
  the unified SA role (role unification), `sa_write_boundaries` (new tool), and resolution of Phase 2's own
  blockers (issue #137 for `classify_resume`, PO topology decision, package-path derivation for semgrep rules).
  All other M-path tools (`arch_set_module`, `module_set_*`, `arch_finalize`) already exist in
  `sa_incremental_mcp.py`. `jig onboard` cannot ship before sa-architect Phase 2 merges.
- **Scanner PII exposure**: The scanner reads arbitrary source files. The scanner prompt must explicitly
  instruct it to exclude secrets-adjacent paths (`*.env`, `*.pem`, credentials dirs) and write only structural
  observations — not raw file contents — to `observations.md`.
- **SA read-mode accuracy**: The SA may misread existing module boundaries. Mitigated by the operator review
  gate — artifacts are inspectable and correctable before backlog creation.
- **Sandbox egress in onboard phase**: The SA read pass may need network egress (context7, WebFetch) for
  library version verification. Onboard uses the same init-phase sandbox as `jig init` — egress must be
  confirmed (same open question as sa-architect) before SA read-pass implementation starts.

## Out of scope

- Backlog generation after onboarding completes (no `--backlog-only` in this feature)
- Onboarding non-git repositories
- Importing existing issue trackers or tickets
- Incremental re-onboard preserving existing tickets (`--force` resets from scratch)
- Test-adequacy review built into the onboard flow

## Open questions

- [ ] **[BLOCKING — planning gate]** sa-architect Phase 2 must define and freeze the unified SA role's `role`
      id and `allowed_tools` list before the SA read-pass step can be planned or implemented. Do not write
      the plan's SA read-pass step until sa-architect Phase 2 ships.
- [ ] **[BLOCKING — shared with sa-architect]** Does the onboard-phase sandbox permit network egress for
      context7 and WebFetch? Resolving this also answers whether the scanner may call context7 for
      dependency-version lookup. Must be confirmed before SA read-pass implementation.
- [ ] The operator review prompt lists artifact counts. Should it also render a diff against any prior
      `.jig/spec/` artifacts (relevant on `--force` re-onboard), or is a count summary sufficient?

## Change log

- 2026-06-07: Initial draft (Brent Hoover)
- 2026-06-09: Write-guard hardening round 7 (roborev job 453, codex per-commit review): the baseline
  ensure-step in `run_onboard` moved after store load and fails closed when a scan-done note exists but
  `scan-guard.json` is missing — the normal CLI entrypoint could previously recreate a deleted baseline
  from the post-scan tree before the scan-pass fail-closed check could see it missing. (brent-hoover)
- 2026-06-09: Write-guard hardening round 6 (roborev job 452): a missing `scan-guard.json` on
  crash-resume fails closed (recomputing would baseline the post-scan tree and pass vacuously); corrupt
  `.jig/config.yaml` fails with guidance everywhere the onboard flow reads it; the between-runs false-alarm
  caveat extends to files created (not just edited) between runs on resume verification. Accepted: the
  PM-confirm SWAP block duplicates ~15 lines from the greenfield loop — pure refactor, deferred to avoid
  further churn on init_workflow in this branch. (brent-hoover)
- 2026-06-09: Write-guard hardening round 5 (roborev job 450): the verified marker is posted only when a
  scan-done note exists (a clean verification of a non-finishing spawn must not vouch for a later scan),
  and classification compares thread indices — verified must postdate scan-done. Corrupt
  `scan-guard.json` and `project.yaml` reads in `run_onboard` fail with `--force` guidance instead of raw
  tracebacks. (brent-hoover)
- 2026-06-09: Write-guard hardening round 4 (roborev job 448): a `scan_guard_verified` note is posted
  after clean verification and classification requires it alongside the scan-done note — a process that
  dies between scan completion and verification now re-enters SCAN_PASS, whose dispatch verifies against
  the persisted baseline without respawning. Corrupt `project.yaml` fails loudly instead of silently
  defaulting guard inputs. Violation messages distinguish pre-dirty paths (possible operator edits between
  runs) from scan-time writes. The review's High (tests in non-git dirs) was a false positive —
  `create_stub` git-inits and the suite is green. (brent-hoover)
- 2026-06-09: Write-guard hardening round 3 (roborev job 446): the guard baseline is persisted at onboard
  start (`.jig/onboard/scan-guard.json`) and every scan verifies against it — a per-spawn snapshot let an
  interrupted scan re-baseline its own tampering; on resume only the paths `run_onboard` itself writes
  (desired-state, config, profiles, workflows) are refreshed. The pre-dirty hash pass exempts `.jig/`
  runtime paths (jig's own stores mutate during the scan; sensitive `.jig` files stay hash-guarded) —
  without this a repo tracking `.jig/` files hit an unrecoverable false BROKEN. `git status -z` replaces
  line parsing so quoted non-ASCII paths aren't invisible to the hash pass. Accepted residuals: the
  baseline file itself is in-repo (an unconstrained Write could alter it — definitive fix is
  transport-level enforcement, deferred with sa-architect Phase 2); `spec_generated` stickiness in
  classification matches greenfield semantics and is revisited when Phase 2 re-enters the flow.
  (brent-hoover)
- 2026-06-09: Write-guard hardening round 2 (roborev job 444): `.git/info/**` and the root `.gitignore`
  join the hash surface (writing exclusion rules to blind the status sweep now trips the hash pass);
  files dirty before the scan are content-hashed so an in-flight working tree's files can't be silently
  rewritten (collapsed untracked directories remain a documented residual); the scan ticket's depth
  budget is recomputed on respawn; `--force` snapshots now include `.jig/roles/*.yaml` (operator-tightened
  roles are security posture). (brent-hoover)
- 2026-06-09: Write-guard hardening (roborev job 442): violations persist as a `scan_guard_violation`
  note that classifies as BROKEN (a bare re-run can't continue past a failed guard; only `--force`
  clears it); `.jig/onboard/desired-state.md` (operator input) joins the protected surface; `git status`
  failure during verification fails loudly; CLAUDE.md creation-allowance keys off onboard-start state in
  project.yaml so a crashed scanner's partial artifact doesn't false-positive the next spawn. (brent-hoover)
- 2026-06-09: Post-scan write verification added (roborev job 439): dropping Bash alone left the
  prompt-scoped Write tool as an indirect code-execution path (`.git/hooks`, `.jig/roles/`). The scan pass
  now hash-snapshots the protected surfaces and sweeps `git status`, failing the onboard on any violation.
  Also: agent-spawn states are capped at 3 consecutive respawns without advancement. (brent-hoover)
- 2026-06-09: Scanner loses Bash access (roborev job 437): the scanner runs on the host against untrusted
  repo content with prompt-level-only constraints, so a prompt-injection payload in the scanned codebase
  must not get a shell. Glob/Grep/Read cover structural scanning. (brent-hoover)
- 2026-06-07: Four reviewer passes applied — PO uses 'brief' ticket; corrected done-signals; depth-bounding
  table added; two-phase SA-done detection; brief_set_section tool name fixed; sa-architect Phase 2 as
  precise prerequisite; module_set_* tool list corrected (sa_write_contracts does not exist); PM_BACKLOG
  gated on .jig/onboard/desired-state.md not docs/brief.md; arch_finalize n_a_categories instruction for
  budget-truncated read pass; planning gate for SA read pass; WorkType.ONBOARD_SCAN; PromptHandler for
  operator review; scanner recommendation pre-injected into PM-1 profile ticket (Brent Hoover)
