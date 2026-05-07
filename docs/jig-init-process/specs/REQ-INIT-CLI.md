---
id: REQ-INIT-CLI
title: jig init CLI entry and dispatch
type: spec
status: draft
owner: brent
created: 2026-04-24
updated: 2026-04-24
depends_on: []
implements: [../problem.md]
---

# jig init CLI entry and dispatch

## Context

The `jig init <name>` command is the user-facing entry point for the
init workflow. It is the only component that owns directory-state
detection, resume vs fresh-init routing, `--force` semantics, and the
top-level conversation loop with the user. All downstream behavior
(brief ticket creation, PO spawn, spec generation, SA/direct branch,
scaffold, summary) is triggered by this component but implemented in
the other specs in this sub-project.

See `../design.md` §Step-by-step narrative steps 1 and 11,
§Resume mechanics, and §Interfaces → CLI.

## Requirements

### REQ-INIT-CLI.1 (ubiquitous)

The CLI shall accept `jig init <name>` with an optional `--force`
flag and no other flags in v1.

**Acceptance:** `jig init --help` lists `<name>` as a required
argument and `--force` as the only flag. Invoking with unknown
flags exits non-zero with a usage error.

### REQ-INIT-CLI.2 (event-driven)

When `jig init <name>` is invoked against a target directory that
does not exist, the CLI shall create the directory, `cd` into it,
and proceed with fresh-init dispatch.

**Acceptance:** After `jig init foo` in an empty parent, `foo/`
exists and the process is in a state that will create `.jig/`.

### REQ-INIT-CLI.3 (event-driven)

When `jig init <name>` is invoked against a target directory that
exists but contains no `.jig/` subdirectory, the CLI shall proceed
as fresh init without prompting.

**Acceptance:** `jig init foo` in a directory containing only
unrelated files proceeds to stub creation.

### REQ-INIT-CLI.4 (event-driven)

When `jig init <name>` is invoked and `.jig/project.yaml` has
`scaffold_applied_at` populated, the CLI shall exit with a non-zero
status and a message pointing the user at the next command.

**Acceptance:** Invoking on a fully initialized project prints
"already initialized" and does not mutate any file.

### REQ-INIT-CLI.5 (event-driven)

When `jig init <name>` is invoked against a directory with a
`.jig/` subtree whose state is neither fresh nor already-scaffolded,
the CLI shall dispatch to resume logic (see REQ-INIT-RESUME).

**Acceptance:** A directory with a brief ticket mid-conversation
re-spawns PO; one with a pending branch decision re-prompts the
branch. No user data is lost.

### REQ-INIT-CLI.6 (event-driven)

When `jig init <name>` is invoked with `--force` on an existing
`.jig/` directory, the CLI shall require a typed confirmation
before wiping `.jig/` state.

**Acceptance:** `--force` prompts `Type 'force' to continue:` and
proceeds only on exact match; any other input aborts without
mutation.

### REQ-INIT-CLI.7 (ubiquitous)

The CLI shall create the initial on-disk stub before spawning any
agent: `.jig/project.yaml` (with `id`, `name`, `created_at`) and
`docs/brief.md` (empty or containing only `# <name>`).

**Acceptance:** After step 2 of the flow, both files exist and
parse; `.jig/spec/architecture.yaml` and
`.jig/spec/project.structured.yaml` do not yet exist.

### REQ-INIT-CLI.8 (ubiquitous)

The CLI shall stream PO output to stdout and user input from stdin
during the brief conversation.

**Acceptance:** A sample PO turn renders to the terminal as it is
produced; the user's reply is captured on newline-submit and
persisted as an `Answer` thread entry.

### REQ-INIT-CLI.9 (event-driven)

When PO emits a `Handoff` to `spec-generator`, the CLI shall spawn
the spec-generator agent and suspend the PO conversation.

**Acceptance:** After `po_finish_brief`, PO's stdin stream is
closed, the generator agent starts, and no further PO turns are
requested until the generator completes.

### REQ-INIT-CLI.10 (event-driven)

When the spec-generator emits `spec_gaps_reported`, the CLI shall
display the gap list and prompt `[R]esume PO / [Q]uit` with `R` as
default.

**Acceptance:** A gap Note containing two items renders both; `R`
on default input re-spawns PO; `Q` writes no further state and
exits 0.

### REQ-INIT-CLI.11 (event-driven)

When the spec-generator emits `spec_generated`, the CLI shall
prompt the branch decision `[Y] SA / [p] Direct pick / [s] Stay on
PO` with `Y` as default.

**Acceptance:** Default input spawns SA; `p` triggers the
direct-pick path; `s` re-spawns PO.

### REQ-INIT-CLI.12 (event-driven)

When SA calls `sa_propose_scaffold`, the CLI shall intercept the
call and prompt `[Y/n/swap]` showing the SA-provided rationale.

**Acceptance:** The rationale string is displayed verbatim; `Y`
advances to scaffold; `n` exits without scaffolding; `swap`
re-enters the SA conversation with the user's new template
preference injected as context.

### REQ-INIT-CLI.13 (event-driven)

When scaffold application completes, the CLI shall print a summary
listing brief, spec, architecture, template, and story commands,
then exit 0.

**Acceptance:** The summary includes the five lines defined in
design step 11 and the exit code is 0.

### REQ-INIT-CLI.14 (unwanted behavior)

If the spec-generator exits without calling either `spec_publish`
or `spec_report_gaps`, the CLI shall prompt `[R]etry / [Q]uit` and
treat the event as an infrastructure failure.

**Acceptance:** A generator that exits cleanly without tool calls
does not advance to the branch prompt; `R` re-spawns the generator;
`Q` exits with state intact for later resume.

### REQ-INIT-CLI.15 (unwanted behavior)

If `.jig/` exists but contains partial filesystem state
inconsistent with any persisted ticket state (e.g. partial
scaffold), the CLI shall exit non-zero and point the user at
`--force`.

**Acceptance:** A directory containing half a scaffolded tree but
no `scaffold_applied` SystemEvent produces a clear error and does
not attempt recovery.

### REQ-INIT-CLI.16 (state-driven)

While a user prompt is displayed (gap-resume, branch decision, SA
confirm, force confirm), the CLI shall accept Ctrl-C and exit 0
with all persisted state intact.

**Acceptance:** Ctrl-C at any prompt leaves `.jig/` in a state
that a subsequent `jig init <name>` can resume from without loss.

## Explicit non-requirements

- TUI integration. CLI-only in v1.
- Additional flags beyond `--force`. No `--resume`, no
  `--reset-init`, no `--continue-anyway`.
- Streaming progress UI during spec generation. The generator runs
  silently; pass/fail is reported once in the gap-or-branch prompt.
- Any override for spec-generator findings — `Q` and `R` are the
  only responses to a gap prompt.

## Open questions

- [ ] Whether `<name>` accepts an absolute path or is relative
  only. Plan proposes relative-only for v1.

## Change log

- 2026-04-24: Initial draft (brent)
