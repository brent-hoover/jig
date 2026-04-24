# Jig Ontology

Shared vocabulary for the jig project. When we disagree on what a word means,
fix it here. When we introduce a new concept, add it here before using it in a
spec or plan.

Kept short on purpose — definitions, not explanations. For deep dives, link to
the authoritative design doc.

## Project artifacts

### Brief
The human-authored Markdown at `.jig/spec/project.md`. First-class. Evolves
over the life of the project. The PO and the user edit it directly. All
other project-level artifacts derive from it.

### Spec (structured)
The machine-generated YAML at `.jig/spec/project.structured.yaml`. A
structured projection of the brief, maintained by the spec agent. Consumed
by tooling that cannot parse Markdown (planning, scaffolding, check rules).
Never authoritative on its own — if brief and spec disagree, brief wins
and the spec is regenerated.

See `docs/02-project-spec.md` for the full data-model design.

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
An addressable unit within the brief — a named scope boundary like
`due-dates` or `task-prioritization`. Lives under
`.jig/spec/capabilities/` in the structured spec. Referenced as
`project://spec/capabilities/<name>`.

### Ticket
A unit of executable work. A capability may spawn zero or more tickets as
the PM elaborates plans.
