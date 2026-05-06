---
title: URI Scheme Extension — Design
type: design
status: draft
owner: brent
created: 2026-05-03
problem: ./problem.md
---

# URI Scheme Extension — Design

## Summary

Extend `jig/spec_uri.py` to support multiple authorities (`spec` / `arch` / `design` / `plan` / `store`), path-style
fragments for sub-element addressing into nested YAML, and optional `@revision:N` time-travel pinning. The existing
`project://spec/...` resolver becomes one of several authority resolvers behind a multi-authority dispatcher;
spec-authority parsing stays binary-compatible. Per-authority resolvers map URIs to file paths + in-file accessors; the
dispatcher caches loaded artifacts at orchestrator-session scope.

## URI grammar

```
project://<authority>/<path>[@revision:<n>][#<fragment>]

authority   := "spec" | "arch" | "design" | "plan" | "store"
path        := "/"-separated path segments; segment alphabet is [a-z0-9_-]
revision    := unsigned integer
fragment    := <anchor-id> | <path-style-fragment>
anchor-id   := matches an {#anchor} definition in the resolved artifact
path-style  := "/"-separated keys into the resolved YAML/JSON structure
```

Examples:

```
project://spec/discovery/personas/merchant
project://spec/suites/catalog/capabilities/normalize-skus
project://spec/suites/catalog/brief#post-a-job
project://arch/architecture
project://arch/architecture@revision:5
project://arch/modules/catalog-ingest/contracts#owns/products/write_access
project://arch/modules/catalog-ingest/contracts@revision:7#owns/products
project://design/wireframes/post-a-job
project://design/system/tokens
project://plan/build/epics/catalog-ingest/layers/bones
project://plan/deferred/d-001
project://store/tickets/t-001
project://store/events?kind=contract_amended    # NOT supported — query strings out of scope
```

Empty fragment is treated as no fragment (existing behavior).

## Per-authority namespaces

### `project://spec/...` (PO output)

| URI pattern | Resolves to |
|---|---|
| `project://spec/project` | `docs/brief.md` (L0 pitch + audience + non-goals) |
| `project://spec/discovery` | `.jig/spec/discovery.md` whole document |
| `project://spec/discovery/personas/<id>` | The persona block (anchor lookup on `{#<id>}`) |
| `project://spec/discovery/journeys/<id>` | The journey block (anchor lookup on `{#<id>}`) |
| `project://spec/discovery/playbacks/<journey-id>` | `.jig/spec/discovery/playbacks/<journey-id>.md` |
| `project://spec/ontology` | `.jig/spec/ontology.md` (project domain vocabulary) |
| `project://spec/ontology/terms/<term>` | A specific term entry (anchor lookup) |
| `project://spec/suites` | `.jig/spec/suites.yaml` whole list |
| `project://spec/suites/<id>` | A suite entry within suites.yaml (path-style fragment-equivalent) |
| `project://spec/suites/<id>/brief` | `.jig/spec/suites/<id>/brief.md` whole document |
| `project://spec/suites/<id>/spec` | `.jig/spec/suites/<id>/spec.structured.yaml` whole structured projection |
| `project://spec/suites/<id>/capabilities/<id>` | A capability within the suite (anchor lookup) |
| `project://spec/suites/<id>/capabilities/<id>/behaviors/<id>` | A behavior within a capability |
| `project://spec/suites/<id>/capabilities/<id>/behaviors/<id>/ac/<id>` | A specific AC |
| `project://spec/project_structured` | `.jig/spec/project.structured.yaml` (federated top-level) |

The existing `jig/spec_uri.py` parser handles all of these as the spec-authority resolver. v2 extension is mostly
additive paths (discovery, ontology, playbacks, suites, behaviors, ac) — no breaking changes to v1 patterns.

### `project://arch/...` (SA output)

| URI pattern | Resolves to |
|---|---|
| `project://arch/architecture` | `.jig/arch/architecture.yaml` whole document |
| `project://arch/data_stores/<id>` | A data store entry within architecture.yaml |
| `project://arch/modules` | The modules list within architecture.yaml |
| `project://arch/modules/<id>` | A module entry within architecture.yaml |
| `project://arch/modules/<id>/contracts` | `.jig/arch/modules/<id>/contracts.yaml` whole document |
| `project://arch/contracts/shared` | `.jig/arch/contracts/shared/` directory listing |
| `project://arch/contracts/shared/<name>` | `.jig/arch/contracts/shared/<name>.yaml` |
| `project://arch/cross_cutting_policies/<id>` | A cross-cutting policy entry |
| `project://arch/risks` | The risk register within architecture.yaml |
| `project://arch/risks/<id>` | A specific risk entry |
| `project://arch/cascades/<risk-id>-<timestamp>` | `.jig/arch/cascades/<risk-id>-<timestamp>.yaml` |
| `project://arch/open_questions/<id>` | An open-question entry within architecture.yaml |

Sub-contract anchoring via path-style fragment is the load-bearing v2 addition. Examples:

- `project://arch/modules/catalog-ingest/contracts#owns/products` — the products ownership clause
- `project://arch/modules/catalog-ingest/contracts#owns/products/write_access` — the write_access list
- `project://arch/modules/catalog-ingest/contracts#integration_ac/shopify-connect` — integration AC for capability
- `project://arch/modules/catalog-ingest/contracts#integration_ac/shopify-connect/must/2` — third "must" clause
- `project://arch/contracts/shared/product#fields/3` — fourth field in the product shape
- `project://arch/architecture#cross_cutting_policies/pii-encrypted-at-rest` — specific policy

The fragment grammar uses the loaded YAML's keys as path segments. Numeric segments index into lists.

### `project://design/...` (VD output)

| URI pattern | Resolves to |
|---|---|
| `project://design/frontend` | `.jig/design/frontend.yaml` whole document |
| `project://design/frontend#stack/styling` | A specific field via path-style fragment |
| `project://design/wireframes` | `.jig/design/wireframes/screens.yaml` (the screen roster) |
| `project://design/wireframes/<screen-id>` | `.jig/design/wireframes/<screen-id>.html` (the wireframe HTML) |
| `project://design/wireframes/<screen-id>/notes` | `.jig/design/wireframes/<screen-id>.notes.md` |
| `project://design/wireframes/index` | `.jig/design/wireframes/index.html` (browser-viewable index) |
| `project://design/system/tokens` | `.jig/design/system/tokens.yaml` |
| `project://design/system/tokens/color/primary` | Specific token via path-style fragment-equivalent |
| `project://design/system/components` | `.jig/design/system/components.yaml` |
| `project://design/system/components/<id>` | A specific component spec |
| `project://design/system/brand` | `.jig/design/system/brand.md` |
| `project://design/references/<filename>` | An operator-supplied reference file |

### `project://plan/...` (PM output)

| URI pattern | Resolves to |
|---|---|
| `project://plan/build` | `.jig/plan/build-plan.yaml` whole document |
| `project://plan/build/epics/<id>` | An epic entry |
| `project://plan/build/epics/<id>/layers/<bones\|mvp\|final>` | A layer within an epic |
| `project://plan/build/cycles/<id>` | A cycle (the dispatched-batch unit) |
| `project://plan/deferred` | `.jig/plan/deferred.jsonl` whole queue |
| `project://plan/deferred/<id>` | A specific deferred item |

### `project://store/...` (runtime state — JSONL stores)

| URI pattern | Resolves to |
|---|---|
| `project://store/tickets/<id>` | A ticket from `.jig/store/tickets.jsonl` |
| `project://store/threads/<ticket-id>` | The thread for a ticket from `.jig/store/comments.jsonl` |
| `project://store/threads/<ticket-id>/entries/<entry-id>` | A specific thread entry |
| `project://store/messages/<id>` | A bus message from `.jig/store/messages.jsonl` |
| `project://store/events` | The full analytics event stream from `.jig/store/events.jsonl` |
| `project://store/events/<event-id>` | A specific analytics event by id |
| `project://store/checkpoints/<id>` | A checkpoint from `.jig/store/checkpoints.jsonl` |

Future additions when the relevant features land:
- `project://learnings/...` (when the learnings layer ships in v2.x — see `docs/v2.0/learnings/`).

## Fragment grammar

Two flavors, distinguished by the resolver based on what the resolved artifact's content type supports:

### Anchor-style fragments

For markdown sources with `{#anchor}` definitions. Existing v1 behavior; unchanged.

```
project://spec/suites/catalog/brief#post-a-job
```

The resolver reads the brief markdown, looks up the `{#post-a-job}` definition, returns the surrounding block. Used for
capability ids in briefs, persona ids in discovery.md, etc. — the established `{#id}` convention from the spec format.

### Path-style fragments

For YAML/JSON sources with nested key structures. New for v2.

```
project://arch/modules/catalog-ingest/contracts#owns/products/write_access
```

The resolver loads the YAML, traverses keys: `owns` → `products` → `write_access`. Array indexing uses numeric segments:

```
project://arch/modules/catalog-ingest/contracts#integration_ac/shopify-connect/must/2
```

Traverses: `integration_ac` (list) → find entry where `capability == "shopify-connect"` → `must` (list) → index 2.

**Resolver disambiguates by source type**: markdown sources → anchor-style; YAML/JSON sources → path-style. A URI with
an obviously-anchor-style fragment (`#single-id`) against a YAML source falls back to looking for a top-level key with
that id. URIs that mix the two (`#some-anchor/then-path`) raise a parse error rather than guessing.

**Caveat about list-index fragments**: `#integration_ac/shopify-connect/must/2` is positional. If the list gets
reordered between cite-time and resolve-time, the URI now points at a different item. Reviewer agents that emit
list-index fragments SHOULD prefer key-based addressing where the schema supports it (e.g.
`#integration_ac/shopify-connect/must` for the whole list, then text-match the specific clause). This is a documentation
discipline, not a code rule — list-index URIs work, but they're brittle and explicitly noted as such in operator-facing
references.

## Versioning — `@revision:N`

Optional revision pin between the path and the fragment. Syntax: `@revision:` followed by an unsigned integer.

```
project://arch/architecture@revision:5
project://arch/modules/catalog-ingest/contracts@revision:7#owns/products
project://spec/suites/catalog/brief@revision:3#post-a-job
```

Resolution: the resolver consults the artifact's `change_log` (which is part of every v2 structured artifact's schema),
materializes the artifact at that historical revision, then applies the fragment lookup against the historical content.

Un-pinned URIs always resolve against the current (latest) revision. Bare URIs in operator-readable text are expected to
be un-pinned; pinned URIs appear in:

- Cascade audit trail (`cascades/<risk-id>-<timestamp>.yaml`) — references contracts at the revision they had when the
  cascade was proposed.
- Ticket `implements_against` field — the contract revision the ticket was implemented against; if the contract later
  changes, the ticket gets stale-flagged.
- Reviewer comments — when a reviewer references "this contract violated revision 5's invariant," pin the reference.
- Analytics events — references to artifacts at event-emission time stay stable.

**Implementation note**: revision history is stored in the artifact's own `change_log` field (per SA design).
Materializing a historical revision means walking the change_log entries newest-to-oldest, undoing each amendment. For
projects with deep histories this is O(amendment-count) — acceptable; revisions aren't queried in hot paths.

## Resolution mechanics

Two-phase: parse (pure string manipulation) → resolve (loads artifact, applies fragment, returns content).

### Parse

```python
@dataclass
class ProjectUri:
    authority: Literal["spec", "arch", "design", "plan", "store"]
    path: list[str]              # segments after authority
    revision: int | None         # from @revision:N suffix
    fragment: str | None         # raw fragment string; resolver disambiguates style
    fragment_style: Literal["anchor", "path", "none"]  # determined at parse if unambiguous

def parse_project_uri(uri: str) -> ProjectUri: ...
```

Parse is pure string ops, fast (~microseconds), no I/O. Validates syntax (authority is recognized, path segments match
`[a-z0-9_-]+`, revision is integer, fragment is well-formed).

### Resolve

```python
@dataclass
class ResolvedUri:
    kind: str           # "module" | "capability" | "contract_clause" | etc. — per-resolver
    data: Any           # the content (dict from YAML, str from markdown, etc.)
    source_path: Path   # the file the resolver loaded
    revision: int       # the revision actually resolved (current if unpinned)

def resolve_project_uri(uri: str | ProjectUri, project_root: Path) -> ResolvedUri: ...
```

Dispatcher routes to the per-authority resolver:

- `spec` → existing `jig/spec_uri.py` resolver, extended with the new path patterns
- `arch` → new `jig/arch/uri.py` resolver
- `design` → new `jig/visual_design/uri.py` resolver
- `plan` → new `jig/plan/uri.py` resolver
- `store` → new `jig/store/uri.py` resolver (uniform across the JSONL stores)

Each resolver knows its file layout under `.jig/`, loads the matching artifact, applies the fragment, returns typed
content.

### Caching

Resolver maintains a per-orchestrator-session cache: `(authority, path-tuple, revision) → loaded artifact`. Invalidation
is event-driven, not mtime-based:

- `ContractAmended` event → invalidate `arch/modules/<m>/contracts*` and `arch/architecture*` cache entries
- `WireframeRevised` event → invalidate `design/wireframes/<screen>*`
- `TicketStateChanged` → invalidate `store/tickets/<id>*`
- etc.

The orchestrator already publishes these events; resolver subscribes at startup. Single-writer discipline (one agent /
orchestrator path mutates each artifact) means cache entries can't go stale silently.

## Pydantic integration

URIs appear as field values in many v2 schemas. Pydantic validators allow parse-only or parse-and-resolve at field load
time:

```python
from typing import Annotated
from pydantic import BaseModel, BeforeValidator
from jig.uri import parse_project_uri, ProjectUri

def validate_project_uri(v: str) -> ProjectUri:
    return parse_project_uri(v)

# Field type alias — parse but don't resolve at load time (resolve is on-demand)
SpecUriField = Annotated[ProjectUri, BeforeValidator(validate_project_uri)]

class Ticket(BaseModel):
    derived_from: SpecUriField
    implements_against: SpecUriField | None = None
```

Resolve-at-load-time variants exist for cases where the artifact must exist (e.g., a contract URI that has to resolve to
a real contract before the ticket is valid). Default is parse-only because most URIs are speculative or used at runtime.

## Programmatic URI construction

Agents and code construct URIs through helper functions, not string templates. One constructor per artifact kind:

```python
from jig.uri import (
    spec_capability_uri,
    spec_persona_uri,
    spec_journey_uri,
    arch_module_uri,
    arch_module_contract_uri,
    arch_module_contract_clause_uri,
    arch_shared_contract_uri,
    arch_risk_uri,
    arch_cascade_uri,
    design_wireframe_uri,
    design_token_uri,
    plan_epic_uri,
    plan_layer_uri,
    plan_deferred_uri,
    store_ticket_uri,
    store_thread_entry_uri,
    store_event_uri,
)

uri = arch_module_contract_clause_uri(
    module="catalog-ingest",
    clause_path=["owns", "products", "write_access"],
)
# → "project://arch/modules/catalog-ingest/contracts#owns/products/write_access"

uri = arch_module_contract_uri(
    module="catalog-ingest",
    revision=7,
)
# → "project://arch/modules/catalog-ingest/contracts@revision:7"
```

Constructors are typed (path segments are explicit args, not free-form strings) so agents can't construct malformed URIs
by accident. Each constructor is a few lines; the surface is large but flat.

## Validation

Three layers, fail-fast at each:

1. **Syntax validation** at parse time. Authority recognized, path segments alphabetically clean, revision integer,
   fragment well-formed. Raises `ProjectUriParseError`.
2. **Resolution validation** at resolve time. Artifact exists, revision exists in change_log if pinned, fragment path /
   anchor exists in the resolved content. Raises `ProjectUriResolveError` with which step failed.
3. **Reference-integrity validation** at artifact-author time. When SA writes a contract that references
   `dependent_contracts: [project://arch/modules/X/contracts#integration_ac/Y]`, the SA's `arch_finalize` MCP tool
   resolves all referenced URIs and rejects the artifact if any don't resolve. Catches broken references at the source
   rather than at consumer time.

Reviewer agents (per PM design) treat references with broken URIs as `contract-violation` comments at the per-commit
mechanical-review cadence; cheap deterministic check.

## Error handling

| Error | When | Behavior |
|---|---|---|
| `ProjectUriParseError` | Syntax violation | Hard error; raises immediately. |
| `ProjectUriUnknownAuthority` | Authority not in {spec, arch, design, plan, store} | Hard error; raises (not user input — code bug). |
| `ProjectUriArtifactNotFound` | Path resolves to a file that doesn't exist | Hard error; raises. |
| `ProjectUriRevisionNotFound` | `@revision:N` references a revision the change_log doesn't have | Hard error; raises. |
| `ProjectUriFragmentNotFound` | Fragment path/anchor doesn't exist in the resolved content | Hard error; raises. |

All errors carry the original URI string + which step failed + (where applicable) closest-match suggestions ("did you
mean `project://arch/modules/catalog-ingest/contracts`?" — fuzzy-match against existing modules).

## Implementation phases

1. **Module structure** — new `jig/uri/` package replacing the single `jig/spec_uri.py`. Existing spec-authority
   resolver moves into `jig/uri/spec.py` unchanged. Dispatcher in `jig/uri/__init__.py`.
2. **Multi-authority dispatcher + parse** — supports all five authorities at parse time; resolves only `spec` initially
   (others raise NotImplementedError but with structured error so callers don't try to parse around it).
3. **Per-authority resolvers** — implement `arch`, `design`, `plan`, `store` resolvers as their respective design docs
   land artifact schemas.
4. **Fragment grammar — path-style** — extend the parser to support path-style fragments; teach the YAML resolvers to
   traverse them.
5. **Versioning** — `@revision:N` parsing + change_log materialization. Lands when SA's contract-amendment workflow is
   implementing the change_log mechanics.
6. **Caching** — resolver session cache + event-driven invalidation. Comes after the resolvers work; cache is pure perf.
7. **Pydantic integration** — typed annotation aliases (`SpecUriField`, etc.) for schema validation.
8. **Programmatic constructors** — one helper per artifact kind. Lands as each artifact kind's resolver lands.

## Risks (of this design)

- **Fragment-style ambiguity.** A URI with `#single-id` against a YAML source could be either an anchor or a one-segment
  path. Resolver tries anchor first, falls back to path. Could lead to confusing errors when the operator means one and
  the resolver picks the other. Mitigation: structured error messages name both attempts; documentation calls out the
  disambiguation rule.
- **List-index fragments are brittle under reorder.** Documented above; not a code rule. Operators / agents who use
  list-index URIs in stable references (audit trails) accept the fragility.
- **Revision materialization performance.** Walking long change_log histories to produce historical revisions could be
  slow on artifacts with hundreds of amendments. Mitigation: snapshot major revisions; change_log entries reference
  snapshots. Out of scope for v2; acceptable as long as amendment counts stay reasonable.
- **Helper-constructor sprawl.** One constructor per artifact kind = ~20+ functions. Mitigation: organize by authority
  (5 modules); keep names predictable (`<authority>_<artifact>_uri`); document grouping in `jig/uri/README.md`.
- **Cache invalidation correctness.** Missing an event subscription means stale cache entries. Mitigation: cache has a
  TTL fallback (5 min) so the worst-case staleness is bounded even if event handling regresses.

## Open questions

(continued from problem.md)

- [ ] **Resolver caching invalidation strategy** — confirmed event-driven primary + TTL fallback.
- [ ] **Unknown-authority handling** — confirmed hard error; treated as code bug not user input.
- [ ] **Array indexing in fragments** — confirmed yes for v2 with documented caveat about reorder fragility.

New ones surfaced during design:

- [ ] **What about querying lists by predicate** rather than index? E.g.
  `project://arch/modules/catalog-ingest/contracts#integration_ac[capability=shopify-connect]/must` — the integration AC
  entry where capability="shopify-connect", then its must list. More robust than positional indexing for lists of
  objects; needs a tiny query language. Probably defer until we feel the pain of positional brittleness.
- [ ] **URI normalization** — should `project://arch/modules/X/contracts/` (trailing slash) and
  `project://arch/modules/X/contracts` resolve identically? Probably yes; parser strips trailing slashes during parse.
  Cheap to add.
- [ ] **Should the dispatcher support resolver plugins** so v2.x extensions (learnings, possibly more) can register
  without modifying core code? Probably yes via a registration decorator; lands when needed, not now.

## Change log

- 2026-05-03: Initial design (brent + claude). Multi-authority extension over the existing `jig/spec_uri.py`. Path-style
  fragment grammar for sub-element addressing into YAML sources. `@revision:N` time-travel pinning resolved through
  artifact `change_log`. Per-authority resolvers under `jig/uri/`. Pydantic field-validator integration. Typed
  programmatic constructors so agents don't string-template. Three-layer validation (syntax, resolution,
  reference-integrity).
