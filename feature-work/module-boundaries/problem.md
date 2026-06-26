---
title: Module Boundaries — Problem Statement
type: problem
status: superseded
owner: brent-hoover
created: 2026-06-08
updated: 2026-06-26
superseded_by: ../../architecture/plan.md
---

# Module Boundaries — Problem Statement

> **Superseded.** Absorbed into `architecture/plan.md` Epic 5 (Enforcement —
> mechanical boundary checks + vocabulary/ontology enforcement).

# Module Boundaries — Problem Statement

## Context

For multi-module projects, the SA decomposes the architecture into modules and records each module
plus its per-module contracts. The module-producing SA path (`sa_incremental_mcp.py` —
`arch_set_module`, the `module_set_*` contract upserts, finalized by `arch_finalize`) writes
`.jig/spec/modules/<m>/contracts.yaml` for every module; this path is exercised today by the bones
simulation scenarios (the default `small`/`medium` profiles still emit a flat single-package spec and
do not yet produce modules — that activation is the deferred Phase 2a SA-unification work, separate
from this).

The whole point of splitting an architecture into modules is isolation: a module should depend only
on the modules and external packages it's meant to, and nothing else. Today that intended isolation
exists only as prose — a `rationale` string, a free-text constraint, the SA's narrative. Dev agents
implement tickets inside modules with no machine-checkable statement of what a module may or may not
import, and nothing inspects their code for violations.

This is the boundaries half (2b) of SA-as-Architect Phase 2. Phase 1 grounded the SA's *technology*
decisions; it did nothing about *module isolation*.

## Problem

Architectural module boundaries are undeclared and unenforced. There is no validated, machine-checkable
record of which other modules a given module may depend on (internal boundaries) or which external
packages it may import (external boundaries), and there is no mechanism that checks a module's code
against such a record. A dev agent can import another module's internals, introduce a forbidden
cross-module dependency, or pull in a banned external library (e.g. `requests` after the project
standardized on `httpx`), and nothing detects it — not the dev gate, not a reviewer, not the human.

The consequence compounds with module count. The more a project leans on decomposition for isolation,
the more an undetected boundary violation entangles unrelated modules; by the time anyone notices, the
violation spans multiple tickets and the cost to unwind has multiplied. Without enforcement, module
decomposition is advisory and erodes silently.

## Complexity drivers

- **Scale**: N/A — bounded by module count per project (single digits in practice); checks run at the
  dev gate against one worktree's diff, not per request or per user.
- **Concurrency**: N/A — boundary declarations are written once per module by the SA at init
  (single-writer); rule generation reads those files and is idempotent.
- **Failure modes**: A missed violation lets architectural erosion compound silently across tickets.
  A false-positive rule blocks legitimate dev work and stalls the run. The enforcement tool may be
  absent in some environments — if it is, the gate must degrade **loudly** (warn), never silently
  pass and report green.
- **Cross-cutting policies**: N/A — no PII/auth/secrets; this is static analysis over source. The one
  obligation is observability: a violation (or a skipped check) must be visible to the dev agent and
  the run record, not swallowed.

## Constraints

- Enforcement must be **static** (no new runtime or import-hook machinery) and must surface through
  the **existing dev phase gate**, not a new enforcement surface. (jig already runs static rules via
  semgrep for canonicalization under `.jig/rules/semgrep/`, so the toolchain exists — but the specific
  tool and rule-file location are design choices, not problem facts.)
- Boundary declarations must fit the SA's existing per-module init-phase output contract: the SA
  already writes per-module artifacts under `.jig/spec/modules/<m>/`; boundaries belong there too.
- A module id is not its Python import path (`job-posting` ≠ `job_posting`), so any check needs a
  module-id → package-path mapping. That mapping is **decided** — a kebab→snake-under-project-package
  convention (project `my-ats` + module `job-posting` → `my_ats.job_posting`), no new required schema
  field — but the underlying fact (the ids and import paths differ) is what constrains the work.
- Python only for v1 — the scaffold templates are Python.
- This half must work against the **current** module-producing SA path (`sa_incremental_mcp` / bones),
  independent of the deferred Phase 2a SA unification. It must not depend on the medium PO topology
  decision.

## Requirements

- A validated, machine-checkable per-module declaration of allowed/forbidden **internal** (cross-module)
  and **external** (package) dependencies.
- A deterministic, idempotent mechanism that derives enforcement rules from those declarations alone.
- A dev phase-gate step that fails loudly, with an actionable message naming the violated rule, when a
  module's code crosses a declared boundary.
- A compliant module's code passes the gate.
- When the enforcement tool is unavailable, the gate warns and does not silently pass.

## Non-goals

- Runtime / import-hook enforcement — static analysis only.
- Non-Python boundary enforcement.
- SA unification (Phase 2a) and the medium PO-topology decision — out of scope; boundaries operate on
  whatever module structure the current SA path produces.
- Activating module production in the default `small`/`medium` profiles — that is Phase 2a.
- Machine-checking prose/code against the ontology terms a module records — the ontology is captured
  for humans/agents, not validated here.

## Success criteria

- Given a project whose modules declare boundaries, a dev agent that writes a forbidden cross-module
  import or a banned external-package import has its phase gate **fail** with a message naming the
  violated rule and module.
- A compliant dev agent's gate **passes**.
- Boundary enforcement rules are regenerated deterministically from the boundary declarations
  (running generation twice yields identical rule files).
- With the enforcement tool absent, the gate emits a warning and the run is not reported as clean.

## Open questions

- [ ] Reconcile rule-file location **and extension**: the Phase 2 design sketched
  `.jig/enforcement/semgrep/boundaries/<m>.yaml`, but existing semgrep rules live under
  `.jig/rules/semgrep/` and are discovered by a `*.yml` glob (`canonicalize.py:130`) — `.yaml` files
  there wouldn't be picked up. One location or two, and which extension? (Design-level.)
- [ ] Where in the dev phase-gate sequence the boundary check runs, and how a violation maps onto the
  existing gate-failure surface dev agents already understand. (Design-level.)
- [ ] A cross-module import ban needs the full set of module ids and their package paths at check
  time, but the check runs against a single dev worktree's diff. Is that module→package mapping
  available inside the dev sandbox/worktree, or only at the orchestrator? This could constrain the
  whole enforcement approach. (Design-level.)

## Change log

- 2026-06-08: Initial draft (brent-hoover)
