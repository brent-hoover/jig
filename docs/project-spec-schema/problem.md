---
title: Project Spec Schema — Problem Statement
type: problem
status: draft
owner: brent
created: 2026-04-27
updated: 2026-04-27
---

# Project Spec Schema — Problem Statement

## Context

`jig init` produces three artifacts: a project brief (`project.md`), a structured projection of that brief (`project.structured.yaml`), and an architecture record (`architecture.yaml`). The brief is human-authored prose owned by the PO. The structured projection is consumed by every downstream agent — SA reads it to choose architecture, ticket-spec writers read it to derive ticket scope, dev agents read it for context.

Today the structured projection has no schema. `handle_spec_publish` does `yaml.safe_load` and writes whatever the spec-generator produced. The spec-generator's prompt points at `docs/reference/02-project-spec.md` for the schema, but that doc is mostly conceptual prose, isn't packaged into the agent's working directory, and the agent ends up hunting for it via filesystem search and inventing fields.

Consumers (SA's prompt today) reference fields like `name`, `summary`, `capabilities`, `non_goals`, `constraints`, `future` — but there's no contract that any of these exist or that they have predictable shapes. SA navigates with `spec_get_field(path)` over a generic dict, every prompt teaches the same loose schema, and downstream tools can't make any guarantees.

The 02 reference doc describes a much richer model: capabilities with stable IDs that survive regeneration, lifecycle states (Idea → Shaping → Ready → In Progress → Built → Archived → Non-goal), URI addressing (`project://spec/capabilities/due-dates`), ticket linkage. None of that is implemented.

## Problem

The structured spec is the contract that lets every downstream piece of jig work coherently, and it currently has no shape. Specifically:

1. **No schema.** Any YAML parses; downstream agents can't trust any field. Validation is whatever spec-generator chose to produce.
2. **No stable identity.** Capabilities have no IDs, so URIs (`project://spec/capabilities/due-dates`) can't resolve and ticket links can't survive renames.
3. **No lifecycle.** State (Built / Planned / In Progress / etc.) isn't represented; "when did this become Built?" isn't answerable from the spec.
4. **No regeneration story.** When the brief changes and spec-gen re-runs, there's no way to preserve metadata that the human format doesn't carry (IDs, timestamps, ticket links).
5. **No format contract for the brief.** The brief is markdown with `## Section`-based sectioning, but capabilities have no IDs, no behaviors, no acceptance criteria — spec-generator has to extract structure from free-form prose, badly.
6. **No URI resolver.** The `project://spec/...` scheme described in the doc isn't implemented. Decision records and ticket links can't reference spec content directly.

Downstream agents reach for `Glob`/`Bash`/`Read` to compensate for what the spec doesn't tell them. Architectural decisions get made against the wrong fields. Ticket linkage to capabilities is impossible. Operators can't trust that the spec captures what they meant.

## Constraints

- The brief stays markdown — humans must be able to read and edit it without tooling.
- Humans never edit `project.structured.yaml` directly. Spec-generator owns it.
- Backward compatibility with the current loose schema is **not** a goal. We're building v1; old `.jig/` directories from prototype runs are disposable (`jig init --force`).
- Validation must run before write. A broken spec is worse than an absent one — downstream agents would silently consume garbage.
- The init flow is the first integration point, but the schema must support the post-init lifecycle (capabilities transitioning through states as tickets ship).

## Requirements

- The structured spec has a Pydantic-validated schema; `spec_publish` rejects anything that doesn't conform.
- Each Capability has a stable, operator-controlled ID that survives regeneration.
- Each Capability has a lifecycle state from a fixed enum.
- The brief carries IDs explicitly (markdown anchor syntax), so spec-gen reads them rather than inferring.
- Spec-gen preserves operator-owned metadata (IDs, aliases, timestamps, ticket links) across regen.
- Acceptance criteria are mandatory for elaborated capabilities (state ∈ {planned, in_progress, built}); spec-gen surfaces missing AC as a blocking gap.
- Non-goals are first-class entities with stable IDs, addressable by URI.
- `project://spec/...` URIs resolve to capability / behavior / non-goal content.
- Downstream agents have capability-aware MCP tools (`spec_list_capabilities`, `spec_get_capability`, etc.), not just generic field-path access.
- The init workflow has a brief-approval step where the operator reviews the rendered brief before spec-gen runs.

## Non-goals

- A `jig spec migrate-v1` CLI for migrating prototype-era specs. Out of scope; `--force` reset is the dev workflow.
- Hard-deletion of capabilities via the brief. Removed-from-brief is a blocking gap; archived state is the way to retire a capability. Hard-delete needs a separate CLI command (deferred).
- Tests-to-AC mapping. AC stay as flat strings for v1; the brief format reserves `[behavior-id]` references vs `{#id}` definitions so AC IDs can be added later without breaking existing briefs.
- BDD-style Given/When/Then for AC. Doc 02 explicitly cautions against this; AC are flat testable strings.
- Capability dependencies / blocks-graph. Useful but defer.
- Sub-element addressing within a capability beyond Behaviors (e.g., addressing individual AC items via `#fragment`). Reserved by the syntax but not implemented.

## Success criteria

- A fresh `jig init` produces a `project.structured.yaml` that passes Pydantic validation.
- Re-running spec-gen against an unchanged brief produces an identical structured.yaml (deterministic).
- Re-running spec-gen against an edited brief preserves all IDs, aliases, timestamps, and ticket links for matched capabilities.
- A removed-from-brief capability surfaces as a blocking gap; the operator can resolve it by editing the brief alone.
- SA's first spawn calls `spec_list_capabilities` + `spec_get_capability(...)` directly without exploratory tool use.
- A `project://spec/capabilities/<id>` URI in a decision record or context bundle resolves to the capability content.

## Open questions

(All answered during brainstorming — see design doc.)

## Change log

- 2026-04-27: Initial draft (brent)
