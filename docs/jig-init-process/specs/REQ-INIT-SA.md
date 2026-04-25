---
id: REQ-INIT-SA
title: Architecture ticket, SA agent, and architecture tool surface
type: spec
status: draft
owner: brent
created: 2026-04-24
updated: 2026-04-24
depends_on: [REQ-INIT-SPECGEN]
implements: [../problem.md]
---

# Architecture ticket, SA agent, and architecture tool surface

## Context

The SA (Systems Architect) path is the default branch after a
valid spec is generated. SA deliberates on architecture shape
with the user using project and scoping questions only — never
tech preferences — reads the structured spec for context, writes
`architecture.yaml` via field-based tools, and proposes a
scaffold template with a rationale the user can accept or
swap.

SA is not allowed to read `project.md` — it consumes the
structured spec instead. This enforces the brief/spec separation
invariant end-to-end.

The architecture ticket (`WorkType.ARCHITECTURE`, reserved id
`"architecture"`) persists the SA conversation and ultimately
records the scaffold decision.

See `../design.md` §Design principles 1, 7, §Step-by-step step 8,
§Interfaces → SA agent MCP tools, §Data model → architecture
schema, §Alternatives → SA asks tech preferences vs
project / scoping questions.

## Requirements

### REQ-INIT-SA.1 (ubiquitous)

The system shall extend `WorkType` with an
`ARCHITECTURE = "architecture"` member.

**Acceptance:** `WorkType.ARCHITECTURE` is importable and
serializes round-trip through ticket store.

### REQ-INIT-SA.2 (event-driven)

When the user selects the SA branch, the orchestrator shall
create an architecture ticket with id `"architecture"` and
`work_type=WorkType.ARCHITECTURE` and spawn an SA agent on it.

**Acceptance:** A single architecture ticket exists after branch
selection. Re-selecting SA is idempotent against an existing
ticket.

### REQ-INIT-SA.3 (ubiquitous)

SA shall receive the contents of
`.jig/spec/project.structured.yaml` as injected input context on
spawn.

**Acceptance:** SA's initial prompt contains spec content; the
agent does not need to call `spec_get_field` to see the overall
shape before its first turn.

### REQ-INIT-SA.4 (ubiquitous)

SA's MCP surface shall include `spec_get_field(path)`,
`spec_list_fields()`, `arch_get_field(path)`,
`arch_set_field(path, value)`, `arch_list_fields()`, and
`sa_propose_scaffold(template_name, rationale, config)`. SA
shall have no tools that read `project.md`.

**Acceptance:** The SA role definition enumerates exactly these
tools for brief/architecture I/O. `brief_*` tools are absent
from SA's surface.

### REQ-INIT-SA.5 (ubiquitous)

`arch_set_field(path, value)` shall atomically write the value
at the given YAML path in `.jig/spec/architecture.yaml`,
creating the file and intermediate keys if needed, and record a
tool-use event on the architecture ticket's thread.

**Acceptance:** Two successive calls on disjoint paths both
persist. A call on a nested path (e.g.
`data_stores.0.type`) creates intermediate structure.
Tool-use events are visible via `jig story architecture`.

### REQ-INIT-SA.6 (ubiquitous)

SA's system prompt shall constrain its user-facing questions to
project and scoping questions only. SA shall not ask the user
for language, framework, or deploy-target preferences.

**Acceptance:** SA role evaluation on sample briefs never
produces a question of the form "Python or TypeScript?",
"Which framework?", or "What deploy target?". Project
questions ("will this have a web interface") and scoping
questions ("how many concurrent users") are allowed.

### REQ-INIT-SA.7 (state-driven)

While the user has volunteered tech preferences in the brief or
in an earlier SA turn, SA shall acknowledge and work with those
preferences but shall not elicit additional tech preferences in
subsequent turns.

**Acceptance:** A brief stating "I want a FastAPI backend with
Postgres" yields an SA that accepts those choices and does not
follow up with "Should we use PostgreSQL or SQLite?".

### REQ-INIT-SA.8 (event-driven)

When SA calls `sa_propose_scaffold(template_name, rationale,
config)`, the system shall record the proposal on the
architecture ticket thread and return control to the CLI for
user confirmation.

**Acceptance:** The proposal is visible as a tool-use event on
the architecture thread; the SA agent suspends pending user
confirmation; the CLI presents the prompt defined in
REQ-INIT-CLI.12.

### REQ-INIT-SA.9 (event-driven)

When the CLI's confirmation prompt returns `swap`, the
orchestrator shall re-spawn SA with the user's new template
preference injected as conversation context.

**Acceptance:** The re-spawned SA sees the prior conversation
plus a new user turn describing the swap request; the prior
`sa_propose_scaffold` tool-use event remains in the thread.

### REQ-INIT-SA.10 (event-driven)

When the user selects the direct-pick branch instead of SA, the
orchestrator shall still create the architecture ticket and
emit an `sa_skipped` SystemEvent on it.

**Acceptance:** Direct-path runs produce an architecture ticket
with exactly one SystemEvent (`sa_skipped`) and zero thread
entries before scaffold.

### REQ-INIT-SA.11 (ubiquitous)

`sa_propose_scaffold` shall require `template_name` to match an
entry in the installed template list; `rationale` to be
non-empty; and `config` to be a dict (empty allowed).

**Acceptance:** A call with an unknown template name errors
before touching the thread. A call with empty rationale errors.

### REQ-INIT-SA.12 (ubiquitous)

SA shall write `rationale` into `architecture.yaml` as a
top-level string field via `arch_set_field("rationale", ...)`
before or as part of calling `sa_propose_scaffold`.

**Acceptance:** After `sa_propose_scaffold` succeeds,
`architecture.yaml` contains a `rationale` field readable by
the CLI for the user confirmation prompt.

## Explicit non-requirements

- Canonical section names or full schema for
  `architecture.yaml`. A starter schema is defined in
  `../design.md`; SA may add fields not in that schema.
  Downstream consumers tolerate unknown keys.
- Schema validation on the `config` dict. Free-form in v1; a
  schema can be added if templates demand it.
- Multi-template scaffolds. SA proposes exactly one template per
  `sa_propose_scaffold` call.
- Automatic template selection from the spec alone. SA is
  allowed zero conversation turns but must still call
  `sa_propose_scaffold` explicitly.
- Tool access to `project.md`. SA has no `brief_*` tools and
  cannot bypass the brief/spec separation.

## Open questions

- [ ] Whether `arch_list_fields` should support recursive
  enumeration or top-level only. Leaning recursive with a depth
  limit.
- [ ] Whether SA should be allowed to write `deferred_decisions`
  as a mechanism for deliberately punting on a decision that
  would otherwise belong in scoping. Leaning yes; the field is
  already in the starter schema.

## Change log

- 2026-04-24: Initial draft (brent)
