---
title: URI Scheme Extension — Problem Statement
type: problem
status: draft
owner: brent
created: 2026-05-03
---

# URI Scheme Extension — Problem Statement

## Context

Jig already has a `project://spec/...` URI scheme implemented in `jig/spec_uri.py`. It addresses the v1 spec (a single
`project.md`) — capabilities, behaviors, non-goals, name, summary. Resolution returns the matching slice of the
structured spec.

The v2 design dramatically expands what artifacts exist and what needs to be addressable:

- Multi-level spec (L0 pitch, L1 discovery with personas/journeys/playbacks, L2 suites, L3 suite briefs, project
  ontology) — `multi-level-spec/design.md`.
- SA architecture artifacts (architecture.yaml, per-module contracts.yaml, shared contract files, risk register, cascade
  audit trail) — `sa-architecture/design.md`.
- VD design artifacts (frontend.yaml, wireframes set, design system tokens/components/brand) —
  `visual-design/design.md`.
- PM build artifacts (build-plan.yaml, deferred queue, ticket extensions) — `pm-workflow/design.md`.
- Runtime store artifacts (tickets, threads, events) — referenced from many places.

Almost every v2 design doc references URIs that the current scheme can't resolve.

## Problem

Three concrete gaps the v2 designs assume but the existing scheme doesn't provide:

**1. Multiple top-level authorities.** Current scheme is hardcoded to `project://spec/...`. v2 needs `project://arch/`,
`project://design/`, `project://plan/`, `project://store/`, plus `project://spec/` evolved to cover the multi-level
artifacts. Without authority-namespacing, contract URIs and spec URIs collide.

**2. Sub-element fragment addressing within structured artifacts.** v2 reviewer agents need to cite specific clauses of
contracts, not just whole files. The SA design commits to:

```
project://arch/modules/catalog-ingest/contracts#owns/products/write_access
```

— a URI that resolves to *the write_access list of the products ownership clause within the catalog-ingest
contracts.yaml*. Current fragment support is anchor-style only (matching `{#anchor}` definitions in markdown); v2 needs
path-style fragment addressing into nested YAML structures.

**3. Versioning / time-travel addressing.** SA contracts have first-class revisions; the cascade-after-impossible-spike
workflow refers to "this contract as of revision 5" — implying URIs like:

```
project://arch/architecture@revision:5
project://arch/modules/catalog-ingest/contracts@revision:7#owns/products
```

The existing scheme has no version-pinning syntax. Without it, "which revision did this ticket implement against"
becomes inferred from event timestamps, which is fragile.

## Simplest possible solution

**Minimum viable extension that satisfies the v2 designs without reinventing:**

- Add an `authority` segment after `project://` (the first segment after the scheme). Values: `spec`, `arch`, `design`,
  `plan`, `store`. Each authority gets its own resolver function; the existing `project://spec/...` parser becomes the
  spec-authority resolver and stays backward-compatible.
- Extend fragment parsing to support path-style fragments (`#a/b/c`) in addition to the existing anchor-style.
  Path-style fragments use `/` as a path separator and resolve as nested-key lookups against the loaded YAML.
- Add an optional `@revision:N` suffix on the path portion (before the fragment) for time-travel. Implementation:
  resolver consults the artifact's change_log to materialize the older revision.

That's enough surface area to satisfy every v2 URI reference. Anything more would be over-design.

## Complications considered

- **Scale**: thousands of cross-references in a medium project's plan + contracts + tickets. URI parsing and resolution
  must be O(few-hundred-microseconds) per call. Forces: parser is pure string manipulation, no I/O; resolver caches
  loaded artifacts within an orchestrator session.
- **Concurrency**: multiple agents resolving URIs against the same artifact concurrently. Forces: resolver is read-only;
  loaded artifacts are immutable snapshots; cache invalidation only on explicit artifact-change events. Single-writer
  discipline (per the SA design) means concurrent reads don't race.
- **Failure modes**: URI references a contract that's been amended; older URI now points to nothing or to different
  content. Forces: explicit `@revision:N` for stable-pin references; un-pinned URIs always resolve against latest;
  reviewer agents that detect "URI X is now stale" emit `ContractReferenceStale` events for the cascade workflow
  (already designed in SA section). Bare URI without revision is always "current" — the responsibility for stable
  pinning is on the citing artifact.
- **Cross-cutting policies**: URIs may be embedded in operator-readable text (briefs, comments). Forces: URI syntax must
  be readable, not opaque (`project://arch/modules/catalog-ingest/contracts#owns/products` is readable; a hash digest
  isn't). Plain-text URIs in markdown should be resolvable without escaping.

Other complications:

- **Backward compatibility with the existing parser** — `jig/spec_uri.py` is in active use. The v2 extension must add
  new resolvers without breaking the spec-authority resolver. Forces: extend, don't replace; spec resolver stays exactly
  as-is and gets re-homed under the multi-authority dispatcher.
- **Validation timing** — URIs in agent output need to be validated quickly so reviewers can flag broken references.
  Forces: parse and resolve are separate; parse fails fast (syntax-only) and resolve fails on first unreachable
  artifact.

## Constraints

- **Must extend `jig/spec_uri.py`, not replace.** The existing API surface (parse, resolve, etc.) for
  `project://spec/...` URIs stays binary-compatible.
- **Must be Pydantic-friendly.** URI strings appear as field values in many v2 schemas (tickets, contracts, plans,
  etc.); Pydantic validators should be able to invoke parse + resolve at validation time.
- **Plain-text readable.** URIs are operator-visible (operators paste them in briefs, comments, decision logs).
  Path-style and anchor-style syntax both stay short enough to type and remember.
- **Tenet 4 (structured language).** URI grammar is itself a structured language; agents construct URIs by composition
  through helper functions, not by string-templating.

## Requirements

- A multi-authority URI grammar covering: `project://spec/`, `project://arch/`, `project://design/`, `project://plan/`,
  `project://store/`. Per-authority resolver functions.
- Fragment grammar supporting both styles:
  - Anchor-style (`#some-id`) — matches `{#some-id}` definitions in markdown sources (existing behavior, keep).
  - Path-style (`#a/b/c`) — nested-key lookup into structured (YAML) sources. New for v2.
- Versioning syntax `@revision:N` on the path portion, before the fragment. Resolves through artifact change_log.
  Optional; un-pinned URIs resolve to current revision.
- Pydantic-friendly validation hooks (validators that parse + optionally resolve at field-load time).
- Programmatic URI construction helpers (one constructor per artifact kind) so agents and code don't string-template.
- Resolution caching at orchestrator-session level; invalidation on artifact-change events.

## Non-goals

- Replacing or rewriting the existing `jig/spec_uri.py`. v2 extension wraps and routes; v1 spec parser stays.
- Cross-project URIs (`project://other-project/...`). Each jig project is self-contained; cross-project references go
  through different mechanisms (eventually, when relevant).
- HTTP-style URI features (`?query=string`, `;params`, `userinfo@host:port`). The grammar is intentionally narrow.
- A general-purpose RESTful API on top of URIs. URIs address artifacts; how they get exposed to external clients is a
  separate question (probably never relevant — operators use the TUI / CLI, not HTTP).
- Full RFC 3986 conformance. The URIs use familiar syntax but we're not implementing the full standard.

## Success criteria

- Every URI referenced in the v2 design corpus parses and resolves correctly.
- Sub-contract anchoring (`#owns/products/write_access`-style) works as designed in the SA + PM reviewer flows.
- `@revision:N` syntax resolves to the correct historical artifact version per the change_log.
- Existing `project://spec/...` URIs (v1 behavior) keep working without modification.
- Pydantic schemas can validate URI fields at load time; bad URIs fail fast with clear error messages.
- Agents construct URIs through helper functions; no string-templating in normal code paths.

## Open questions

- [ ] **Resolver caching invalidation strategy.** Cache by artifact-mtime, or by explicit "this artifact changed" event?
  Probably explicit event — the orchestrator already publishes `ContractAmended` / `WireframeRevised` / etc. — resolver
  subscribes and invalidates accordingly.
- [ ] **What happens if a URI's authority is unknown?** Hard error (raise) or soft fail (return None + log)? Lean hard
  error — unknown authority is a bug, not user input.
- [ ] **Should fragments support array indexing?** E.g. `#integration_ac/shopify-connect/must/2` — third "must" clause.
  Useful for citing specific items; ambiguous when the list reorders. Probably yes for v2 with a "be cautious about
  list-index URIs after reorder" caveat in docs.
