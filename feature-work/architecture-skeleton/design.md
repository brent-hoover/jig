---
title: Architecture Skeleton — Design
type: design
status: superseded
superseded_by: ../sa-architect/design.md
owner: brent
created: 2026-06-07
updated: 2026-06-07
problem: ./problem.md
---

# Architecture Skeleton — Design

## Summary

Add per-module `boundaries.yaml` to the medium-profile architecture skeleton. `sa_mvp` produces one
boundaries file per module during `arch_finalize`, declaring the module's domain ontology, which other
modules it may import from, and which external libraries it may use. A `generate_boundary_rules` step
compiles these declarations into semgrep rules that run in the dev agent phase gate, making boundary
violations mechanically uncrossable rather than advisory.

## Approach

### Artifacts

Three artifact types form the full medium-profile skeleton, all produced by `sa_mvp`:

| Artifact | Location | Purpose |
|---|---|---|
| `architecture.yaml` | `.jig/spec/` | Modules, data stores, shared contracts, cross-cutting policies |
| `contracts.yaml` | `.jig/spec/modules/<m>/` | Exposed APIs, emitted events, behavioral/data contracts |
| `boundaries.yaml` | `.jig/spec/modules/<m>/` | Domain ontology, inter-module dependency rules (new) |

PO produces suites and specs (unchanged).

### `sa_mvp` per-module pass

`sa_mvp` already runs in two phases: produce `architecture.yaml`, then produce per-module `contracts.yaml`.
Boundaries slot into the same second phase. For each module, SA calls `sa_write_contracts` then
`sa_write_boundaries` — both complete before moving to the next module. The ordering matters: SA must
have the full module graph settled (architecture.yaml done) before it can reason about who may call whom.

SA reasoning guidance for boundaries:
- **Ontology**: derive domain terms from `module.summary`, `module.implements_capabilities`, and
  `module.intent`. Define each term in this module's context specifically — not the generic definition.
- **Internal allowed/forbidden**: start from `consumes_apis`/`consumes_events` edges in `architecture.yaml`
  to seed `allowed_modules`. Then reason about encapsulation: which modules does this one have no business
  calling directly? Those go in `forbidden_modules`.
- **External**: derive from `ExternalDependency` entries in `contracts.yaml`. A module may only use the
  external services it explicitly declares as dependencies.

### Semgrep rule generation

`generate_boundary_rules(project_path: Path)` runs at the end of `arch_finalize`, after all
`boundaries.yaml` files are written. It reads every `modules/<m>/boundaries.yaml` and writes one semgrep
rule file per module to `.jig/enforcement/semgrep/boundaries/<m>.yaml`.

Rule shape per module:

- **`forbidden_modules`**: one rule per forbidden module — pattern matches any import of that module's
  package path from within this module's package directory.
- **`allowed_modules` (whitelist)**: one rule matching any import of `<project>.<module>` for any module
  NOT in the allowed list, scoped to this module's directory. Requires the project package name to be
  known at generation time (read from `architecture.yaml` or project config).
- **`forbidden_external`**: one rule per forbidden external library — pattern matches `import <lib>` or
  `from <lib> import ...` within this module's directory.
- **`allowed_external` (whitelist)**: analogous to internal whitelist, scoped to external imports.

Both allowed and forbidden lists may coexist. The effective policy is: imports in `allowed_modules` AND
NOT in `forbidden_modules` are permitted.

Generation is idempotent. Re-running after an operator edits a `boundaries.yaml` overwrites the
corresponding rule file.

### Phase gate integration

The existing dev agent phase gate (ruff + tests) gains a semgrep step. If
`.jig/enforcement/semgrep/boundaries/` exists and is non-empty, semgrep runs against the agent's worktree
before the gate passes. Violations surface the semgrep output to the agent as a gate failure message — the
agent sees which import triggered which rule and must fix it before the phase advances.

If semgrep is unavailable in the sandbox, the gate emits a warning and continues (no silent skip — the
warning surfaces in the ticket thread).

## Interfaces

### `BoundariesFile` schema (new, `jig/schemas/arch.py`)

```python
class OntologyTerm(BaseModel):
    term: str         # domain concept name (e.g. "Candidate", "Pipeline")
    definition: str   # what it means in this module's context specifically

class InternalBoundaries(BaseModel):
    allowed_modules: list[str] = Field(default_factory=list)  # module ids — whitelist, empty = no whitelist
    forbidden_modules: list[str] = Field(default_factory=list) # module ids — blacklist
    rationale: str | None = None

class ExternalBoundaries(BaseModel):
    allowed: list[str] = Field(default_factory=list)    # external dependency ids — whitelist
    forbidden: list[str] = Field(default_factory=list)  # external dependency ids — blacklist
    rationale: str | None = None

class BoundariesFile(BaseModel):
    spec_version: int = 1
    module: str  # kebab-case, must match a module id in architecture.yaml
    ontology: list[OntologyTerm] = Field(default_factory=list)
    internal: InternalBoundaries = Field(default_factory=InternalBoundaries)
    external: ExternalBoundaries = Field(default_factory=ExternalBoundaries)
    change_log: list[ChangeLogEntry] = Field(default_factory=list)
```

### `sa_write_boundaries` MCP tool (new, `jig/init_mcp.py`)

Parallel to `sa_write_contracts`. Accepts a `BoundariesFile` payload, validates against schema, writes to
`.jig/spec/modules/<m>/boundaries.yaml`. Returns an error if the module id does not exist in
`architecture.yaml`.

### Generated semgrep rules (`.jig/enforcement/semgrep/boundaries/<m>.yaml`)

Standard semgrep YAML format. One file per module, regenerated by `generate_boundary_rules`. These files
are committed to the project repo so operators can inspect and override them.

## Data model

`BoundariesFile` is a new pydantic model in `jig/schemas/arch.py`, following the same pattern as
`ContractsFile`. Persisted as YAML at `.jig/spec/modules/<m>/boundaries.yaml`.

## Alternatives considered

### Extend `Module` in `architecture.yaml`

Add `forbidden_modules` and ontology fields directly to the `Module` schema. SA writes one file.
Rejected because it conflates "what a module is" with "what it may call" — different concerns with
different change cadences. `architecture.yaml` also grows large for medium+ projects.

### Extend `CrossCuttingPolicy` with module scoping

Add per-module scoping and a `semgrep_pattern` field to `CrossCuttingPolicy`. Rejected because
`CrossCuttingPolicy` was designed for architecture-wide rules; adding per-module scoping would bend its
semantics into something different and complicate the existing negative-polarity rule path.

### Chosen: per-module `boundaries.yaml`

Clean separation of concerns, mirrors the `contracts.yaml` pattern, independently readable and editable
by operators, straightforward to compile to semgrep. The extra file per module is worth the clean boundary
between "what the module does" (contracts) and "what it may touch" (boundaries).

## Risks

- SA may produce boundaries that are too restrictive, causing legitimate agent imports to fail the gate.
  Mitigated by the operator review step after `arch_finalize` and before the first dev ticket.
- The whitelist semgrep pattern (flag any import not in the allowed list) requires knowing the full project
  package layout at generation time. If the scaffold uses a non-standard package structure, the generated
  rules may be incorrect. Mitigation: derive package paths from `architecture.yaml` module ids using a
  configurable prefix; document the convention.
- Semgrep is already in the stack but may not be available in all sandbox configurations. The gate
  degrades to warn-only rather than block if semgrep is absent.

## Out of scope

- Runtime (import hook) enforcement of boundaries.
- Boundary enforcement for languages other than Python.
- Automatic inference of boundaries from an existing codebase (onboarding scenario).
- Enforcement of data write access (handled by `OwnedCollection.write_access` in contracts.yaml).
- The `classify_resume` fix needed to unblock `sa_mvp` on the medium profile — tracked separately in
  issue #137.

## Open questions

- [ ] Rule regeneration trigger: CLI command (`jig arch regenerate-rules`), MCP tool callable by SA
      during a delta-pass, or automatic on `boundaries.yaml` mtime change at gate time? Needs decision
      before implementation.
- [ ] Package path derivation: how does `generate_boundary_rules` map module id `job-posting` to Python
      package `ats.job_posting`? Options: (a) project-level `package_prefix` in config, (b) explicit
      `package_path` field on `Module`, (c) derive by convention (kebab → snake, prefix from project
      name). Needs decision before schema is finalised.

## Change log

- 2026-06-07: Initial draft (brent)
