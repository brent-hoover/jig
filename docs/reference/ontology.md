# Jig Ontology

Shared vocabulary for the jig project. When we disagree on what a word means,
fix it here. When we introduce a new concept, add it here before using it in a
spec or plan.

Kept short on purpose — definitions, not explanations. For deep dives, link to
the authoritative design doc.

## Project artifacts

### Brief
The human-authored Markdown at `docs/brief.md`. First-class. Evolves
over the life of the project. The PO and the user edit it directly. All
other project-level artifacts derive from it.

### Spec (structured)
The machine-generated YAML at `.jig/spec/project.structured.yaml`. A
structured projection of the brief, maintained by the spec agent. Consumed
by tooling that cannot parse Markdown (planning, scaffolding, check rules).
Never authoritative on its own — if brief and spec disagree, brief wins
and the spec is regenerated.

See `docs/reference/02-project-spec.md` for the full data-model design.

### Brief / spec separation
A hard invariant. `project.md` (the brief) is human-facing. `project.structured.yaml`
(the spec) is agent-facing. **Only the PO and the spec-generator ever read the brief.**
Every other agent — SA, PM, workers — works off the spec. This keeps the human's
document a narrative while giving agents a stable, structured contract. Any agent
design that requires a non-PO agent to read the brief is wrong.

### Architecture
The machine-generated YAML at `.jig/spec/architecture.yaml`. Authored by the
SA (Systems Architect) on the SA path, or populated from template metadata on
the direct path. Structured rather than narrative because it is an agent-to-agent
artifact — downstream context hydration consumes it. Required keys on every
init: `template`, `template_applied_at`, `sa_path`, `language`, `framework`.
SA-path extras include `rationale`, `config`, `tech_decisions` (SA grounded technology choices with source
provenance — each entry carries a `source_type` of `context7`, `live_fetch`, `operator_specified`, or `inferred`),
`size` (project scale classification: `S` or `M`), and optional structured fields (data stores, external services,
deferred decisions).

## Roles

### PO (Product Owner)
Owns the brief and, by extension, the structured spec. Talks to the user to
produce and evolve the brief. Recommends templates. Drafts capability specs.
Model tier: Opus.

### PM (Project Manager)
Owns issues and plans derived from the brief. Opens tickets, sequences
work, maintains the project plan. Model tier: Sonnet.

## Work units

### Capability
A named product feature or functional area — what the product does for
its users (e.g. `due-dates`, `task-prioritization`). The PO identifies
capabilities while authoring the brief. Each one appears as a subtree
in the structured spec, addressable as
`project://spec/capabilities/<name>`. Tickets implement capabilities.

### Ticket
A unit of executable work. A capability may spawn zero or more tickets as
the PM elaborates plans.
