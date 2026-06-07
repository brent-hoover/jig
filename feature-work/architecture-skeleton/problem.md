---
title: Architecture Skeleton — Problem Statement
type: problem
status: active
owner: brent
created: 2026-06-07
updated: 2026-06-07
---

# Architecture Skeleton — Problem Statement

## Context

When jig initialises a medium-profile project, the SA agent produces `architecture.yaml` (modules, data stores,
shared contracts, cross-cutting policies) and — via `sa_mvp` — per-module `contracts.yaml` files (exposed APIs,
emitted events, behavioral and data contracts). The PO produces suites and specs describing user journeys and
acceptance criteria.

Together these artifacts are intended to form an "architecture skeleton" that guides dev agents to implement the
right thing in the right place. In practice the skeleton is incomplete: it describes module responsibilities and
inter-module APIs, but it does not declare which modules are *allowed* to communicate with each other, what
vocabulary belongs to each module's domain, or enforce any of these rules at commit time.

The concept of "context as code" (see O'Reilly article) frames this gap well: SA produces advisory prose rather
than machine-enforceable constraints. A dev agent that ignores or misreads the architecture suffers no mechanical
consequence until a human reviewer catches the violation — often several tickets later.

## Problem

The medium-profile architecture skeleton is missing two things that would make it actionable rather than advisory:

1. **Boundaries**: Each module has no declared policy for which other modules it may import from and which
   external services it may use directly. A dev agent working on `candidates` has no mechanical signal telling
   it not to import from `notifications` or call Stripe directly. Violations are silent and accumulate across
   tickets.

2. **Ontology**: Each module has no declared vocabulary — the domain terms it owns and their definitions in
   this module's context. Without this, agents leak terminology across module boundaries (defining `Candidate`
   in the `jobs` module, for instance) and produce architecturally incoherent codebases even when individual
   tickets pass review.

Both problems share a root cause: the SA produces structured data (modules, contracts) but stops short of
encoding the *constraints* those structures imply. The enforcement gap means the skeleton is only as good as
each agent's interpretation of it.

## Simplest possible solution

Add a prose `boundaries.md` file per module that SA writes during init. Agents read it before implementing
tickets. No schema, no enforcement — just text.

This doesn't scale and doesn't enforce. The value of boundaries is making violations impossible to commit, not
making them easier to catch after the fact.

## Complications considered

- **Scale**: N/A — bounded by module count per project, which is small (typically 5–15 modules). Rule
  generation and semgrep execution scale linearly with module count and are not a concern.
- **Concurrency**: N/A — SA runs once per project init, single-writer. Semgrep runs per-agent but is
  read-only on the rule files.
- **Failure modes**: If SA produces incorrect boundaries (too permissive or too restrictive), dev agents
  will either silently violate architecture or fail gates on legitimate imports. The operator must be able
  to review and correct boundaries before development begins. Rule generation must be re-runnable after
  operator edits.
- **Cross-cutting policies**: Boundary enforcement runs in the dev agent phase gate. If semgrep is
  unavailable in the sandbox, the gate must degrade gracefully (warn, not block) rather than failing
  silently.

## Constraints

- Boundaries and ontology are produced by `sa_mvp` in the same pass that produces `contracts.yaml` — no
  new init phase or new agent role.
- The `BoundariesFile` schema must be machine-readable and validated at write time, not just at gate time.
- Semgrep rule generation must be idempotent and re-runnable.
- The operator must be able to review and edit boundaries before the first dev ticket runs.
- No new external dependencies beyond semgrep, which is already in the stack.

## Requirements

- SA produces `modules/<m>/boundaries.yaml` for every module during `arch_finalize`, alongside
  `contracts.yaml`.
- `boundaries.yaml` declares: (a) the module's domain ontology (terms + definitions), (b) which other
  modules it may import from (`allowed_modules` whitelist, optional), (c) which other modules it must
  not import from (`forbidden_modules` blacklist, optional), (d) which external libraries/services it
  may use (`allowed` external), and (e) which it must not use (`forbidden` external).
- A `generate_boundary_rules` step runs after `arch_finalize` and writes semgrep rules to
  `.jig/enforcement/semgrep/boundaries/`.
- The dev agent phase gate runs semgrep against boundary rules. Violations fail the gate.
- Rules are regenerated whenever a `boundaries.yaml` file changes.

## Non-goals

- Enforcing data write access at the code level — that is handled by `OwnedCollection.write_access` in
  `contracts.yaml` and is a separate enforcement concern.
- Runtime enforcement of boundaries (e.g. import hooks, monkey-patching). Compile-time semgrep is
  sufficient and less fragile.
- Boundaries for languages other than Python.
- Automatic inference of boundaries from existing code (onboarding scenario — separate feature).

## Success criteria

- Running `jig init` against an ATS brief on the medium profile produces `boundaries.yaml` for each
  module with sensible `allowed_modules`/`forbidden_modules` and a coherent ontology section.
- Semgrep rules are generated and a manually introduced boundary violation is caught at the phase gate.
- An agent that writes a cross-boundary import fails the gate and receives the semgrep error.
- Operator can edit a `boundaries.yaml` file, re-run generation, and the updated rules take effect on
  the next agent run.

## Open questions

- [ ] Should `allowed_modules` enforcement use a whitelist-only semgrep pattern (flag any import NOT in
      the list) or generate explicit per-forbidden-module rules? The whitelist approach is stronger but
      requires knowing the full module package path mapping at generation time.
- [ ] What triggers rule regeneration mid-project — a CLI command, an MCP tool, or automatic detection
      of `boundaries.yaml` mtime changes?

## Change log

- 2026-06-07: Initial draft (brent)
