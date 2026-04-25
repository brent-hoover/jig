---
id: REQ-INIT-BRIEF
title: Brief ticket, PO agent, and brief tool surface
type: spec
status: draft
owner: brent
created: 2026-04-24
updated: 2026-04-24
depends_on: [REQ-INIT-CLI]
implements: [../problem.md]
---

# Brief ticket, PO agent, and brief tool surface

## Context

The brief is the human-authored Markdown at `.jig/spec/project.md`.
PO is the agent that collaborates with the user to author and evolve
it during init. The brief ticket (`WorkType.BRIEF`, reserved id
`"brief"`) is the persistence surface for every PO turn, user reply,
tool call, and handoff. This spec covers the ticket lifecycle, the
PO agent's spawn and re-spawn behavior, and the brief-specific MCP
tool surface exposed to PO.

PO is the only agent other than the spec-generator that is allowed
to read `project.md`. Every other agent in jig works off the
generated structured spec.

See `../design.md` §Design principles 1, 3, 6, §Step-by-step steps
3–5, §Interfaces → PO agent MCP tools, and
`../../reference/ontology.md` → Brief / spec separation.

## Requirements

### REQ-INIT-BRIEF.1 (ubiquitous)

The system shall extend `WorkType` with a `BRIEF = "brief"` member.

**Acceptance:** `WorkType.BRIEF` is importable; existing
serialization of tickets and thread entries handles the new value
without migration.

### REQ-INIT-BRIEF.2 (event-driven)

When the CLI triggers fresh brief creation, the orchestrator shall
create a ticket with id `"brief"` and `work_type=WorkType.BRIEF`
if no such ticket exists.

**Acceptance:** After stub creation, `ticket_store.get("brief")`
returns a ticket whose `work_type` is `BRIEF`. Re-invoking brief
creation is idempotent: no duplicate ticket, no error.

### REQ-INIT-BRIEF.3 (event-driven)

When the brief ticket is created, the orchestrator shall spawn a
PO agent on that ticket with the standard MCP surface plus the
brief-specific tools defined in this spec.

**Acceptance:** The spawned agent process has access to
`brief_get_section`, `brief_list_sections`, `brief_set_section`,
and `po_finish_brief` tools in addition to the standard jig MCP
tools.

### REQ-INIT-BRIEF.4 (ubiquitous)

PO's system prompt shall constrain its scope to product concerns
as defined by the human-authoring format in
`docs/reference/02-project-spec.md`: product shape (what it is,
for whom), capabilities across lifecycle states (Built, Planned
committed, Planned not committed, Backlog), and Non-goals.
Language, framework, and deploy-target elicitation are out of
scope for PO.

**Acceptance:** The PO role definition references the reference
doc as the authoritative format. A PO agent asked about tech
stack redirects the user to the SA path or direct path.

### REQ-INIT-BRIEF.5 (event-driven)

When the user submits input during a PO turn, the system shall
persist it as an `Answer` thread entry on the brief ticket (or as
a `Note` if the user submitted input without a preceding PO
question).

**Acceptance:** `jig story brief` after three user turns and two
PO questions shows all five entries in order with correct types.

### REQ-INIT-BRIEF.6 (event-driven)

When PO emits a question, the system shall persist it as a
`Question` thread entry on the brief ticket before the CLI
displays it.

**Acceptance:** A PO question is visible in the thread before the
user's reply is solicited; a Ctrl-C after PO's question but before
the user replies resumes with PO's question re-displayed.

### REQ-INIT-BRIEF.7 (ubiquitous)

PO shall call `brief_get_section(name)` or
`brief_list_sections()` before every action that depends on brief
content.

**Acceptance:** PO's role definition requires a re-read before
every tool call or user-facing turn. A user edit to
`project.md` between turns is observable in PO's next response.

### REQ-INIT-BRIEF.8 (ubiquitous)

`brief_set_section(name, markdown)` shall atomically replace the
named section of `project.md`, creating the section if it does
not exist. The `name` refers to a top-level (`##`) section from
the format defined in `docs/reference/02-project-spec.md`
(e.g. `Planned (committed)`, `Non-goals`).

**Acceptance:** A section replace on a file open in another
editor does not corrupt the file. Setting a nonexistent section
appends it. Each call emits a tool-use event on the brief
ticket's thread.

### REQ-INIT-BRIEF.9 (event-driven)

When PO calls `po_finish_brief(summary)`, the system shall emit a
`Handoff` thread entry on the brief ticket with
`target_role="spec-generator"` and the provided summary as
content.

**Acceptance:** `spec_store`-style inspection of the brief
thread shows exactly one `Handoff` after `po_finish_brief`;
re-invoking `po_finish_brief` after a handoff already exists is
an error.

### REQ-INIT-BRIEF.10 (state-driven)

While the user is editing `project.md` out-of-band (in an
external editor), the system shall not lock the file and shall
tolerate arbitrary edits.

**Acceptance:** A user replacing the entire file with handwritten
content between PO turns does not crash PO; PO's next
`brief_get_section` reads the new content.

### REQ-INIT-BRIEF.11 (event-driven)

When the brief ticket is resumed (see REQ-INIT-RESUME), the
orchestrator shall re-spawn PO with the full existing thread as
context.

**Acceptance:** Re-running `jig init` mid-conversation yields a
PO that acknowledges prior exchanges and does not repeat questions
already answered.

### REQ-INIT-BRIEF.12 (unwanted behavior)

If PO attempts to call `po_finish_brief` while the brief contains
no content (empty file or only the `# <name>` header), the tool
shall reject the call and return a descriptive error.

**Acceptance:** `po_finish_brief` on an empty brief returns an
error; the brief ticket thread receives no `Handoff`.

## Explicit non-requirements

- Redefining the brief format. Canonical sections and authoring
  conventions are already defined in
  `docs/reference/02-project-spec.md`; this spec consumes that
  format and does not modify it.
- Edit detection / drift warnings. If the user edits the brief
  out-of-band, PO simply re-reads; the reactive spec agent that
  watches for edits is a separate sub-project.
- Brief versioning or history beyond what the ticket thread
  provides. `project.md` is not versioned separately.
- Brief authorship mode selection (PO-authored vs user-authored
  vs collaborative). v1 is collaborative; endpoints of the
  spectrum emerge from user behavior.

## Open questions

- [ ] Whether `brief_set_section` should support section removal
  (setting to empty string) or require an explicit delete tool.
  Leaning toward empty-string = remove for tool-surface minimalism.

## Change log

- 2026-04-24: Initial draft (brent)
- 2026-04-24: Reference docs/reference/02-project-spec.md as the
  authoritative brief format (state-category sections, level-3
  capability headers). Removed the "section-name vocabulary"
  non-requirement; PO consumes the existing format. (brent)
