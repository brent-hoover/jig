---
title: jig init process — Problem Statement
type: problem
status: draft
owner: brent
created: 2026-04-24
updated: 2026-04-24
---

# jig init process — Problem Statement

## Context

Today `jig init <name>` creates a project directory, asks for a
template name, and scaffolds. That is the entire flow. There is no
mechanism for the user to describe what they're building, no
conversation with a product-focused agent, no architectural deliberation,
and no translation of the user's intent into a form that downstream
agents can consume.

The reference docs (`docs/reference/02-project-spec.md`,
`docs/reference/04-ownership.md`) already describe the target state:
a human-authored brief at `.jig/spec/project.md` owned by the user and
a Product Owner (PO) agent, plus a machine-generated structured spec
at `.jig/spec/project.structured.yaml` that downstream agents consume.
The Systems Architect (SA) owns `.jig/spec/architecture.yaml`. None of
this exists yet at runtime — the docs describe the intended model;
init doesn't implement it.

The consequence: `jig start` has nothing to hand downstream agents. No
product context, no architecture context, no stable contract for what
we're building. "Init" today produces a scaffolded directory but not
a working starting state.

This is the first of four sub-projects decomposed from the broader
"project.md / spec / PO process." It covers the entry path only — the
PO conversation, the SA/architecture branch, scaffold, and the
one-shot translation of brief → spec. It ends at the moment
`jig start` could legitimately spawn a worker.

## Problem

`jig init` does not produce the artifacts or the context that the rest
of jig is designed to consume. Specifically:

- No mechanism for the user to author `project.md` collaboratively
  with a PO.
- No brief-to-spec translation, so agents downstream have no
  machine-readable project context to work from.
- No architecture conversation, so the template choice is opaque and
  the `architecture.yaml` file that downstream context composition
  will depend on is never written.
- No resumability — the current flow is one-shot. A Ctrl-C mid-init
  means starting over.
- No principled separation between "document for humans" and
  "document for agents." This separation is an explicit design goal
  of jig and has to be established at init time or it never gets
  established.

## Constraints

- Must fit jig's async Python orchestrator model. No new processes,
  no external services.
- Must reuse existing ticket, thread, and bus infrastructure
  (`TicketStore`, `ThreadStore`, MCP, SystemEvent). Adding new thread
  entry kinds is a last resort.
- PO and SA are existing first-class owner roles — not new roles
  (see `docs/reference/04-ownership.md`).
- **Brief / spec separation is load-bearing.** No agent other than
  the PO and the spec-generator ever reads `project.md`.
  Every other agent (SA, PM, workers) consumes
  `project.structured.yaml`.
- Init must be Ctrl-C safe at any point. Re-running `jig init <name>`
  resumes from the last persisted state.
- User always has complete control over `project.md`. They can edit
  it in any editor at any point during the PO conversation; the PO
  re-reads from disk before every action.
- CLI-first. TUI parity not required in v1.

## Requirements

- `jig init <name>` produces three artifacts: `.jig/spec/project.md`
  (brief), `.jig/spec/project.structured.yaml` (spec), and
  `.jig/spec/architecture.yaml`.
- The PO conversation is persisted as thread entries on a reserved
  `WorkType.BRIEF` ticket with id `"brief"`.
- The SA conversation (when opted in) is persisted on a reserved
  `WorkType.ARCHITECTURE` ticket with id `"architecture"`.
- After PO finishes the brief, a one-shot spec-generator agent
  produces the structured spec and validates it against the brief.
  Validation includes schema-level checks (required fields,
  parseability) and semantic checks (contradictions, ambiguities
  between sections).
- A failed spec generation blocks progression to SA and scaffold.
  The user's only paths forward are to resume the PO conversation or
  quit and return later.
- The user can opt out of the SA conversation and pick a template
  directly; even in that path a valid spec is still required.
- Template application records the chosen template and timestamp in
  `.jig/project.yaml`, seeds or finalizes `architecture.yaml`, and
  emits a `scaffold_applied` SystemEvent on the architecture ticket.
- Re-running `jig init <name>` on an in-progress directory resumes
  from the persisted state (PO mid-conversation, awaiting
  branch decision, SA mid-conversation, awaiting scaffold
  confirmation, spec generation pending, or partially scaffolded).
- `jig story brief` and `jig story architecture` produce coherent
  narratives of the respective conversations and decisions.

## Non-goals

- **Reactive spec agent.** Edit detection on `project.md`, drift
  warnings, automatic regeneration on user out-of-band edits,
  ongoing issue surfacing. The one-shot generator triggered by init
  lands here; the reactive layer is a separate sub-project.
- **PM role, issues, plans, capability specs, worker agent flows.**
  Everything between "init produces a working starting state" and
  "first capability is delivered" is out of scope.
- **Template-context library.** `architecture.yaml` captures the
  template decision; the mechanism that uses that decision to
  compose context for downstream agents (e.g. "when it's a FastAPI
  project, workers get FastAPI context") is a separate sub-project.
- **Brief format definition.** The human-authoring format for
  `project.md` is already defined in
  `docs/reference/02-project-spec.md` (state-category sections,
  level-3 capability headers, bullets for one-liners, intro
  paragraph for product shape). This sub-project consumes that
  format; it does not redefine it.
- **Transactional scaffolding.** A half-applied scaffold errors and
  points the user at `--force`. Staging / rollback is deferred.
- **TUI integration.** CLI-only in v1.
- **Safer-than-`--force` escape hatch** (e.g. `--reset-init` that
  wipes only jig state and leaves user-added files alone). v1 has
  `--force` only.
- **Streaming progress UI during spec generation.** Silent run,
  pass/fail reported in the summary.
- **Override mechanism for spec-generator findings.** If the user
  disagrees with a reported gap or contradiction, the resolution
  path is to clarify the brief via PO — not a CLI flag that
  bypasses validation.

## Success criteria

- A user runs `jig init <name>`, converses with PO until the brief
  is complete, optionally converses with SA or picks a template
  directly, and ends with a project directory containing all three
  artifacts plus scaffolded code.
- `jig start` run after init succeeds has real project context
  available to downstream agents via the structured spec.
- `jig init <name>` run on an in-progress directory resumes from
  the last persisted state without loss.
- `jig story brief` and `jig story architecture` show the full
  setup trail: PO questions and answers, spec-generator outcome,
  branch decision, SA conversation if any, scaffold event.
- Opting out of SA does not leave downstream agents without context
  — a minimal `architecture.yaml` stub is written and the structured
  spec still exists.
- An under-specified or internally contradictory brief cannot
  produce a scaffolded project. The user is routed back to PO.

## Open questions

- [ ] Exact initial template set. Likely two or three in v1
  (e.g. python-api, python-cli, typescript-cli). Locked during
  implementation.
- [ ] Whether the spec-generator's `Gap` payload lives as a
  structured extension of `Note` or as its own thread entry type.
  Design doc will decide; default leaning is reuse `Note` with
  structured content to avoid new entry kinds.

## Change log

- 2026-04-24: Initial draft (brent)
- 2026-04-24: architecture.md → architecture.yaml (agent-to-agent
  artifact should be structured, not markdown) (brent)
- 2026-04-24: Brief format is defined in
  docs/reference/02-project-spec.md; removed "canonical section
  names" from non-goals and open questions (brent)
