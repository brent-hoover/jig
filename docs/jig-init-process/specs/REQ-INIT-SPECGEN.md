---
id: REQ-INIT-SPECGEN
title: Spec-generator agent and validation gate
type: spec
status: draft
owner: brent
created: 2026-04-24
updated: 2026-04-24
depends_on: [REQ-INIT-BRIEF]
implements: [../problem.md]
---

# Spec-generator agent and validation gate

## Context

The spec-generator is a one-shot, non-conversational agent that
translates `project.md` (the brief) into
`project.structured.yaml` (the spec) and validates the projection
against the brief. It runs between PO's handoff and the
SA/direct branch decision. A failing run blocks progression to
scaffold — init cannot produce a valid project tree on top of an
invalid brief.

The generator is one of only two agents allowed to read
`project.md` (PO is the other). It does not converse with the
user: it reads, validates, and calls exactly one of two
completion tools.

See `../design.md` §Step-by-step step 6, §Interfaces →
Spec-generator agent MCP tools, §Data model → Gap payload,
§Risks → semantic validation false positives.

## Requirements

### REQ-INIT-SPECGEN.1 (event-driven)

When the CLI observes a `Handoff` with
`target_role="spec-generator"` on the brief ticket, the CLI shall
spawn the spec-generator agent with read-only access to
`.jig/spec/project.md` and the spec schema definition.

**Acceptance:** The generator process starts within one dispatch
cycle of the handoff. The generator has no write access to
`project.md`. The generator does not receive stdin.

### REQ-INIT-SPECGEN.2 (ubiquitous)

The spec-generator shall terminate by calling exactly one of
`spec_publish(yaml, advisory_notes)` or
`spec_report_gaps(gaps)`.

**Acceptance:** Tool-call telemetry on the generator process
shows exactly one completion-tool invocation. A generator that
exits without either is handled per REQ-INIT-CLI.14.

### REQ-INIT-SPECGEN.3 (event-driven)

When `spec_publish(yaml, advisory_notes)` is called, the system
shall atomically write `.jig/spec/project.structured.yaml`,
emit a `spec_generated` SystemEvent on the brief ticket, and
post any advisory notes as a single `Note` thread entry.

**Acceptance:** The spec file exists and parses; the SystemEvent
is visible via `jig story brief`; advisory notes (if any) are
readable as a Note.

### REQ-INIT-SPECGEN.4 (event-driven)

When `spec_report_gaps(gaps)` is called, the system shall write
no spec file, post a `Note` thread entry on the brief ticket
carrying the gap list as structured payload, and emit a
`spec_gaps_reported` SystemEvent.

**Acceptance:** `.jig/spec/project.structured.yaml` is absent or
unchanged; the brief ticket's most recent `Note` has a `payload`
dict with a `gaps` key containing the full list; the SystemEvent
is visible.

### REQ-INIT-SPECGEN.5 (ubiquitous)

The gap payload shall carry each entry as a dict with keys
`kind`, `location`, `description`, `suggested_question`
(optional), and `severity`.

**Acceptance:** A gap payload with all five keys deserializes
into the expected shape. `kind` is one of `missing`,
`contradiction`, `ambiguity`, `under_specified`. `severity` is
one of `blocking`, `advisory`.

### REQ-INIT-SPECGEN.6 (state-driven)

While gaps with `severity: blocking` exist, the generator shall
route to `spec_report_gaps` and not publish.

**Acceptance:** A brief that produces one blocking and two
advisory gaps yields a `spec_report_gaps` call, not a
`spec_publish` call. Advisory-only gap sets may be attached to a
`spec_publish` call as `advisory_notes`.

### REQ-INIT-SPECGEN.7 (ubiquitous)

The generator shall perform schema-level validation on the
generated YAML (parseability, required fields, type
correctness) before calling `spec_publish`.

**Acceptance:** A malformed YAML projection triggers
`spec_report_gaps` with a `kind: missing` or
`kind: under_specified` gap pointing at the schema violation,
not a publish.

### REQ-INIT-SPECGEN.8 (ubiquitous)

The generator shall perform semantic validation of the
projection against the brief, flagging contradictions between
sections and direct ambiguities as gaps.

**Acceptance:** A brief with a stated success criterion that
contradicts a stated non-goal produces at least one
`kind: contradiction` gap. Calibration of the semantic-validation
threshold is tuned via the generator's system prompt during
implementation.

### REQ-INIT-SPECGEN.9 (ubiquitous)

The spec-generator shall have no conversational surface and no
access to stdin or stdout.

**Acceptance:** The generator role definition excludes any
chat/question tools; it cannot emit `Question` thread entries.

### REQ-INIT-SPECGEN.10 (event-driven)

When the generator is re-spawned after a prior
`spec_gaps_reported` (see REQ-INIT-RESUME), it shall receive the
full brief ticket thread (including the prior gap Note) as
context.

**Acceptance:** The generator's input prompt references the prior
gap list; its re-run is not blind to the prior findings.

### REQ-INIT-SPECGEN.11 (unwanted behavior)

If both `spec_publish` and `spec_report_gaps` are called from the
same generator run, the second call shall error and the first
result stands.

**Acceptance:** A generator that calls `spec_publish` then
attempts `spec_report_gaps` sees the second call rejected; spec
file is present and a `spec_generated` SystemEvent exists.

### REQ-INIT-SPECGEN.12 (state-driven)

While no spec file exists, init shall not advance to the branch
prompt or scaffold.

**Acceptance:** CLI state transitions gate on presence of
`spec_generated` SystemEvent, not on absence of gap Notes or
other proxies.

## Explicit non-requirements

- Reactive regeneration triggered by user edits to
  `project.md`. The generator is one-shot per run; the reactive
  agent is a separate sub-project.
- Override mechanism to publish over reported gaps. The only
  resolution path for reported gaps is to clarify the brief via
  PO.
- Streaming progress UI during generation. Silent run; outcome is
  surfaced by the CLI after the tool call.
- Automatic retry on generator crash. User re-runs `jig init`;
  resume logic detects the incomplete state and re-spawns the
  generator (see REQ-INIT-RESUME).
- A separate thread-entry kind for gaps. Gaps are carried as
  structured payload inside a `Note`.

## Open questions

- [ ] Whether the generator should emit a `Handoff` back to PO
  when it reports gaps, or whether the CLI's resume-PO prompt is
  handoff enough. Leaning CLI-only for now; Handoff can be added
  if story-trail coherence suffers.

## Change log

- 2026-04-24: Initial draft (brent)
