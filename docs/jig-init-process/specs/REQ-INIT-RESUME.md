---
id: REQ-INIT-RESUME
title: Resume-from-state detection and dispatch
type: spec
status: draft
owner: brent
created: 2026-04-24
updated: 2026-04-24
depends_on: [REQ-INIT-CLI, REQ-INIT-BRIEF, REQ-INIT-SPECGEN, REQ-INIT-SA, REQ-INIT-SCAFFOLD]
implements: [../problem.md]
---

# Resume-from-state detection and dispatch

## Context

Init is Ctrl-C-safe at every step. Re-running `jig init <name>`
against an in-progress directory must pick up exactly where the
previous run left off, with no state loss and no duplicated work.
There is no separate state machine: thread entries and
SystemEvents on the two reserved tickets (`"brief"`,
`"architecture"`) *are* the state. This spec defines how the CLI
inspects persisted records and routes to the correct in-flight
step.

See `../design.md` §Design principle 4, §Resume mechanics,
§Risks → agent crash mid-flow, half-applied scaffold.

## Requirements

### REQ-INIT-RESUME.1 (event-driven)

When `jig init <name>` is invoked against a directory with an
existing `.jig/` subtree that does not have
`scaffold_applied_at` set in `project.yaml`, the CLI shall
classify the state and route to the matching resume action.

**Acceptance:** Every resumable state defined below is detected
without prompting the user for context. Unrecognized states
trigger the `--force` error path (REQ-INIT-CLI.15).

### REQ-INIT-RESUME.2 (state-driven)

While the brief ticket is open with no `Handoff` thread entry,
the resume action shall be to re-spawn PO with the full brief
thread as context and continue the brief conversation.

**Acceptance:** A Ctrl-C during PO's third question resumes to
PO displaying its prior question and awaiting user input.

### REQ-INIT-RESUME.3 (state-driven)

While the brief ticket has a `Handoff` to `spec-generator` but
no `spec_generated` SystemEvent and no gap Note, the resume
action shall be to re-spawn the spec-generator agent.

**Acceptance:** A Ctrl-C while the generator is running (or a
generator crash) resumes by re-running generation against the
current brief.

### REQ-INIT-RESUME.4 (state-driven)

While the brief ticket has a gap Note with no subsequent user
decision recorded, the resume action shall be to re-prompt the
`[R]esume PO / [Q]uit` choice with the gap list redisplayed.

**Acceptance:** A Ctrl-C at the gap prompt resumes with the
same gap list visible and the same default (`R`).

### REQ-INIT-RESUME.5 (state-driven)

While the brief ticket has a `spec_generated` SystemEvent but
no architecture ticket exists, the resume action shall be to
re-prompt the branch decision `[Y] SA / [p] Direct / [s] Stay`.

**Acceptance:** A Ctrl-C after spec generation but before the
user answered the branch prompt resumes at that prompt.

### REQ-INIT-RESUME.6 (state-driven)

While the architecture ticket is open with no
`sa_propose_scaffold` tool-use event and no `sa_skipped`
SystemEvent, the resume action shall be to re-spawn SA with
the structured spec and prior thread as context.

**Acceptance:** A Ctrl-C during SA's conversation resumes to
SA continuing its prior turn without re-asking answered
questions.

### REQ-INIT-RESUME.7 (state-driven)

While the architecture ticket has a `sa_propose_scaffold`
tool-use event with no subsequent accept, reject, or swap
outcome recorded, the resume action shall be to re-prompt
`[Y/n/swap]` with the SA-authored rationale redisplayed.

**Acceptance:** A Ctrl-C at the SA confirmation prompt resumes
at that prompt with the correct rationale string.

### REQ-INIT-RESUME.8 (state-driven)

While the architecture ticket has an `sa_skipped` SystemEvent
but no `scaffold_applied` SystemEvent, the resume action shall
be to re-display the direct-pick template list.

**Acceptance:** A Ctrl-C before the user selected a template in
the direct-pick path resumes with the list redisplayed.

### REQ-INIT-RESUME.9 (event-driven)

When resume detects that `scaffold_applied` is present on the
architecture ticket, the CLI shall error per REQ-INIT-CLI.4
(already initialized).

**Acceptance:** A completed init re-invoked errors and exits
non-zero without touching any file.

### REQ-INIT-RESUME.10 (event-driven)

When resume detects partial filesystem state inconsistent with
any persisted ticket state (e.g. scaffolded files present but
no `scaffold_applied` SystemEvent; missing `project.md` but
brief ticket populated; tickets referencing reserved ids with
wrong `work_type`), the CLI shall error per REQ-INIT-CLI.15
pointing the user at `--force`.

**Acceptance:** Artificially induced inconsistent states fail
fast with a clear message; no attempt is made to reconcile.

### REQ-INIT-RESUME.11 (ubiquitous)

Resume detection shall be pure inspection of persisted records
(ticket thread, SystemEvents, filesystem) with no side effects
prior to dispatch.

**Acceptance:** Running resume detection multiple times in a
row (dry-run) produces the same classification each time and
does not modify any file, ticket, or thread entry.

### REQ-INIT-RESUME.12 (ubiquitous)

Reserved ticket ids (`"brief"`, `"architecture"`) shall be
located by id lookup, not by `work_type` query.

**Acceptance:** A project with an unrelated ticket that
happens to share a `work_type` of `BRIEF` or `ARCHITECTURE`
does not interfere with resume classification.

## Explicit non-requirements

- Automatic crash recovery or retry loops. Resume is triggered
  by the user re-running `jig init`, never automatically.
- Safer escape hatches than `--force`. A future `--reset-init`
  that preserves user-added files is out of scope.
- Reconciliation of inconsistent state. Any detected
  inconsistency errors; no automatic repair.
- Persisted progress for the spec-generator's internal run
  (e.g. partial YAML written). Generation is one-shot per run;
  a crashed run is re-run from the start.

## Open questions

- [ ] Whether resume should offer the user an explicit status
  summary ("you were at: PO conversation, resuming…") before
  acting, or silently re-spawn. Leaning silent for v1; the
  agent's next turn is the status summary.

## Change log

- 2026-04-24: Initial draft (brent)
