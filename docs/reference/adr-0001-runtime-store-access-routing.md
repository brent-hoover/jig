---
id: ADR-0001
title: Runtime store access uses typed StoreAuthority ports; project:// URIs are the cross-boundary serialization surface
type: decision
status: accepted
owner: brent-hoover
created: 2026-06-28
updated: 2026-06-28
supersedes: []
superseded_by: null
---

# ADR-0001: Runtime store access uses typed StoreAuthority ports; `project://` URIs are the cross-boundary serialization surface

## Status

accepted

## Context

Epic 2 (Substrate) MVP task 1 reads: *"Route all store access through `StoreAuthority` (existing stores become
internal impls)."* `StoreAuthority` already exists as a `project://` read/write facade with an authority allow-list,
per-instance write authorization, an opt-in cache, and (for `project://store/tickets/<id>`) a write path that routes
through the typed `TicketStore`. The open question — which `plan.md` explicitly flagged — is *how* the ~72 in-engine
store call sites should "route through" it.

A scan of current access shows it is dominated by **rich, typed domain operations**, not CRUD:

- `tickets.get` ×111, `threads.post` ×79, `tickets.create` ×35, `threads.for_ticket` ×32, `tickets.update` ×23,
- plus `find_ready`, `find_by_parent`, `find_by_assignee`, `update_status`, `set_create_callback`,
  `set_status_change_callback`, …

These are not "write this dict" endpoints. `TicketStore.create()` allocates the `jig-N` key under a cross-process
lock, fires create callbacks, and enforces domain transitions; `ThreadStore.post()` takes typed entries and performs
legacy read-migration. The coarse URI surface (`read(uri) -> dict`, `write(uri, dict)`) only addresses
fetch-one / write-one / list-all and returns plain dicts.

Forcing every runtime path through the URI surface would be a **semantic regression**, not just churn: rich queries
get reimplemented over list-dumps, the typed `Ticket` / `ThreadEntry` models are lost, and create/transition/callback
semantics have to be re-threaded. Conversely, leaving runtime access as scattered, directly-constructed concrete
stores never establishes an authority boundary at all.

## Decision

We adopt a **hybrid split by accessor / audience**, implemented with the cheap mechanical shape of a provider model,
under one hard rule that prevents it from becoming "two unrelated doors":

1. **Runtime / in-engine code uses typed domain ports vended by `StoreAuthority`.** Consumers obtain
   `store.tickets`, `store.threads`, `store.memory`, … from the authority instead of constructing or holding their own
   store. Call sites keep their typed methods unchanged (`self.tickets.find_ready()` → `self.store.tickets.find_ready()`).
   Ports are exposed as protocols where practical, not a forever-promise of concrete classes.

2. **`project://` URIs are the cross-boundary serialization surface** — used for cross-authority references,
   agent/MCP access, citations, review evidence, and external tooling. `read(uri)` returns **dicts on purpose**: it is
   a serialization boundary for portable evidence, *not* the internal data model. Runtime code keeps `Ticket`,
   `ThreadEntry`, etc.

3. **The URI door delegates to the same typed stores/ports the engines use.** There is exactly one canonical
   implementation of every store behavior. `read("project://store/tickets/<id>")` calls the typed store and serializes
   the model; `write("project://store/tickets/<id>", dict)` delegates through the `TicketStore` create/update path
   (schema validation, transition gate, key allocation, callbacks). No separate URI implementation re-parses JSONL or
   reimplements queries — that is how drift happens.

4. **`StoreAuthority` is the composition root for store lifecycle, policy, and cache coherence.** It owns
   construction/loading of the concrete stores, owns URI resolution/adapters, and owns cache-invalidation policy. Every
   typed write reachable through a vended port invalidates the matching `project://store/...` URI cache entries (the
   changed object **and** affected list views). Without this, the hybrid fails exactly where critics predict.

5. **Separate two policy concepts that "authority" currently overloads:**
   - **Authoring authority** — ownership of `spec` / `arch` / `design` / `plan` (the existing `writable=(...)`
     allow-list; an authoring engine scoped to `("spec",)` legitimately may not write `project://arch/...`).
   - **Runtime store capability** — finer-grained capabilities for mutable runtime state
     (`can_create_ticket`, `can_update_ticket_status`, `can_post_thread`, `can_write_checkpoint`, `can_write_memory`).
   Core orchestration must mutate runtime state even though `store` is not an "ownership" boundary like `spec`/`arch`.
   The exact capability mechanism is deferred (see Consequences → Neutral); this ADR only fixes that the two concepts
   are **distinct** and must not collapse into one allow-list.

We reject "URIs for everything" (option B) for runtime paths, and we treat the plain provider model (option A) only as
the **migration mechanism**, not the end-state — its end-state holes (typed access bypassing URI policy/cache, URI vs
typed drift, `StoreAuthority` degenerating into a bag of concrete stores) are closed by rules 3–5.

## Consequences

### Positive

- Runtime keeps typed models, rich queries, and domain semantics (create/transition/callback/key-allocation) — no
  semantic regression on hot paths.
- One canonical store implementation behind both doors; the URI surface is a faithful projection, so cross-boundary
  reads/writes can't silently diverge from in-engine behavior.
- The bulk migration is mechanical and reviewable: change *where the handle comes from*, not behavior.
- `StoreAuthority` becomes a real authority boundary (lifecycle + policy + cache), the precondition for later swapping a
  backing store without touching consumers.

### Negative

- `StoreAuthority` grows a typed accessor per store type — more surface than a single `read`/`write` pair (mitigated by
  exposing ports/protocols rather than concrete classes).
- Two access idioms coexist (typed vs URI); the rule "URI delegates to typed" is load-bearing and must be enforced in
  review, not just intended.
- Cache coherence now spans both doors: every typed write path must invalidate URI cache entries, adding a wiring
  obligation at the boundary.

### Neutral / trade-offs

- The **runtime-capability** model (`can_create_ticket`, …) is named here but its enforcement mechanism is deferred to
  a follow-on — this ADR fixes the *concept split*, not the API.
- Tests may still instantiate concrete stores directly when unit-testing store behavior; only production orchestration
  must route through `StoreAuthority`.

## Alternatives considered

### B — `project://` URIs for everything (rejected for runtime)

Rewrite all ~72 sites to `read(uri) -> dict` / `write(uri, dict)`. Optimizes uniformity at the wrong layer: the scan
shows runtime is dominated by rich operations, so this is a semantic regression (lost types, reimplemented queries,
re-threaded create/transition/callback semantics) on top of large, risky churn. URIs remain the right tool where
*addressability* matters (cross-authority, external/agent, evidence) — not where *behavior* matters.

### A — plain provider model (accepted only as the migration mechanism, not the end-state)

`StoreAuthority` exposes whatever concrete stores exist and consumers fetch them. Cheap and mechanical, but as an
end-state it leaves real holes: typed access bypasses URI authorization/cache, URI and typed paths drift, cache can be
correct for URI writes yet stale after typed writes, and `StoreAuthority` becomes a bag of concrete stores rather than
an authority boundary. We keep A's mechanics but close those holes via decision rules 3–5 ("URI delegates to typed",
cache invalidation at the boundary, authority-vs-capability split).

## Migration approach

The **decision rules above are binding; the ordering below is illustrative, not prescriptive.** We sequence by
whatever fits best within the scope of the work already in flight — not a fixed checklist. The only invariants per
slice are: it **preserves behavior**, it is **independently reviewable/shippable**, and it carries **one integration
assertion** proving the slice still composes (e.g. create via typed port → read same object via URI; update via typed
port → URI read reflects it; write via URI → same typed callbacks/rules fire).

The pieces of work this comprises (done in whatever order is convenient):

- **`StoreAuthority` as composition root** — it owns construction/loading of `TicketStore`, `ThreadStore`,
  `MemoryStore`, … and vends typed accessors (ideally protocols/ports).
- **URI store *reads* delegate to the typed stores** — `project://store/tickets/<id>`, `threads/<id>`, list-all call
  the typed store and serialize the model (URI door = projection over canonical behavior).
- **URI *writes* delegate to typed operations** — keep routing through `TicketStore` etc. so validation, transition
  gates, key allocation, and callbacks stay intact; no raw collection writes behind the facade. (Tickets are already
  done; extend to other writable collections as wired.)
- **Cache invalidation at the `StoreAuthority` boundary** — every typed write reachable through a vended port
  invalidates matching URI cache entries. This is the guardrail that makes the hybrid coherent.
- **Migrate construction sites, not behavior** — `self.tickets` → `self.store.tickets`, etc.; method calls unchanged.
  This is the reviewable ~72-site mechanical step, naturally chunked by vertical runtime owner (orchestrator, ticket
  MCP/CLI/TUI front doors, thread/comment paths, simulator driver, checkpoints/memory/review stores, tests).
- **Remove direct store construction from consumers** — concrete stores stay internal to the substrate package;
  production orchestration does not construct them. (Tests may still instantiate concrete stores when unit-testing
  store behavior.)
- **URI-only external surfaces** — MCP/agent/cross-authority access prefers URIs; in-engine scheduling does not.

Chunk these into behavior-preserving PRs however the in-flight work makes most sense.

## References

- `architecture/plan.md` → Epic 2 — Substrate (Store + Bus); MVP task 1 and the `StoreAuthority` open-question note.
- `architecture/jig-instance.md` → Substrate design.
- GitHub #216 (Epic 2 MVP tracker); PRs #227 (write gate), #228 (URI read), #229 (URI write via TicketStore),
  #230 (typed bus).

## Change log

- 2026-06-28: Proposed, then accepted (brent-hoover)
