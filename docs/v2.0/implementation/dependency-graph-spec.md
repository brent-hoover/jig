# Typed Dependency Graph — Spec Draft

## Purpose

Build a queryable graph that answers "what could this ticket break?" at
spawn time. The graph stitches together modules, contracts, tickets,
tracers, and the public surface (routes / events / migrations / env
vars / external APIs) so reviewers, planners, and the spawn-context
resolver all draw from one coherent impact view.

## Non-goals at MVP

- No static-import-graph parsing. The graph is **declared**, not
  **discovered**. If a module imports another without declaring it in
  `consumes_*`, the graph won't know.
- No real-time PR-diff analysis. The trigger is artifact change, not
  commit diff.

## Architecture

The graph is derived from existing v2 artifacts plus a few small
additions. Storage is materialised but cheaply rebuildable.

```
sources:
  Architecture          (data_stores, modules)
  ContractsFile         (per-module: exposes, emits, owns,
                         external_dependencies, contracts)
  SuitesIndex           (suites, capabilities)
  DiscoveryDoc          (journeys, capability_roster)
  BuildPlan             (epics)
  Ticket                (module_id, capability_ids, epic_id, ...)
  Risk                  (status, dependent_contracts)

↓ derive (jig/graph/derive.py)

.jig/graph/dependency-graph.yaml          (master snapshot)
.jig/graph/ticket-impact/<ticket_id>.yaml (per-ticket projection)

↓ consume

  jig/graph/query.py        → MCP tools, CLI
  jig/runtime.py            → spawn-context selection
  jig/reviewers/dispatch.py → reviewer targeting
  jig/planner_pm_mcp.py     → tier promotion
  jig/analytics/            → impact metrics
```

## Schema additions

### `Module` (jig/schemas/arch.py)

Add declared-consumption fields so the graph can build `consumes`
edges from authored data:

```python
class ApiConsumption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    module: str   # provider module id
    name: str     # api name as declared in provider's exposes[]

class EventConsumption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    module: str   # publisher module id
    name: str     # event name as declared in publisher's emits[]

class Module(BaseModel):
    # ... existing fields ...
    consumes_apis: list[ApiConsumption] = Field(default_factory=list)
    consumes_events: list[EventConsumption] = Field(default_factory=list)
```

`arch_finalize` already runs reference checks; extend it to verify each
consumption resolves to a real `ExposedAPI` / `EmittedEvent` on the
named provider. Fails loud at finalize.

### `Ticket` (jig/ticket.py)

The flat `module_id + capability_ids` pair bootstraps the graph, but
reviewers and the planner benefit from richer declarations on tickets
that legitimately cross boundaries (refactors, composition tickets):

```python
class TicketTouches(BaseModel):
    model_config = ConfigDict(extra="forbid")
    modules: list[str] = []                # supplements module_id
    capabilities: list[str] = []           # supplements capability_ids
    exposed_apis: list[str] = []           # "<module>:<name>"
    emitted_events: list[str] = []
    consumed_events: list[str] = []
    data_stores: list[str] = []
    behavioral_contracts: list[str] = []
    data_contracts: list[str] = []
    routes: list[str] = []                 # "<METHOD> <path>"
    migrations: list[str] = []
    env_vars: list[str] = []
```

Backward-compatible: existing tickets default to empty `touches`, and
the graph falls back to `(module_id, capability_ids)` to root the
impact view.

### `ContractsFile.exposes` (jig/schemas/arch.py)

The `ExposedAPI.kind` literal already accepts
`function | class | endpoint | cli | other`. Add `migration` and
`env_var` so those nodes are first-class without inventing a new
artifact.

## Graph types — `jig/graph/types.py`

```python
class Node(BaseModel):
    id: str                # "module:checkout-api", "event:order.created"
    kind: Literal[
        "module", "capability", "epic", "ticket", "tracer",
        "exposed_api", "emitted_event", "data_store",
        "behavioral_contract", "data_contract",
        "owned_collection", "external_dependency",
        "risk", "journey",
    ]
    title: str | None = None
    uri: str | None = None  # project:// URI back to source artifact

class Edge(BaseModel):
    src: str
    dst: str
    kind: Literal[
        "owns", "exposes", "emits", "consumes",
        "calls", "depends_on", "backed_by",
        "implements", "covers", "touches",
        "exercises", "validates", "blocks", "uses",
    ]

class DependencyGraph(BaseModel):
    spec_version: int = 1
    generated_at: datetime
    nodes: list[Node]
    edges: list[Edge]

    def neighbors(self, node_id: str, depth: int = 1,
                  kind: str | None = None) -> set[str]: ...
    def consumers_of(self, node_id: str) -> set[str]: ...
    def reachable_from(self, node_id: str) -> set[str]: ...

class TicketImpact(BaseModel):
    ticket_id: str
    generated_at: datetime
    touched: list[Node]                  # directly reached nodes
    consumers: dict[str, list[Node]]     # "what consumes node X"
    exercised_tracers: list[Node]
    crossed_boundaries: int              # complexity signal for planner
```

## Derivation — `jig/graph/derive.py`

```python
def build_graph(project_root: Path) -> DependencyGraph:
    """Materialise the graph from authored artifacts. Pure function."""

def write_graph(project_root: Path) -> Path:
    """Build + write .jig/graph/dependency-graph.yaml."""

def ticket_impact(
    graph: DependencyGraph, ticket_id: str, *, depth: int = 1,
) -> TicketImpact:
    """Project the graph around a ticket's touches. Cheap; no I/O."""
```

Rebuild triggers:
- After `arch_finalize` / `module_set_*` (Architecture changed)
- After per-module contracts changes
- After `plan_finalize` / ticket creation/update
- On `jig graph build`

The build is cheap (seconds even with hundreds of modules), so rebuild
on natural triggers rather than diffing.

## MCP tools — `jig/graph_mcp.py`

```
graph_get_impact(ticket_id) -> TicketImpact
graph_neighbors(node_id, depth=1, kind?) -> list[Node]
graph_consumers_of(node_id) -> list[Node]
graph_tracers_for(node_id) -> list[Tracer]
graph_changed_interfaces(ticket_id) -> list[Node]   # vs. last green tracer
```

Wired into `create_agent_mcp_server` for:
- All operational roles (dev, test, sa, vd) — `graph_get_impact`,
  `graph_neighbors` for context selection.
- Reviewers — `graph_consumers_of` to scope findings.
- Coordinator/PM — `graph_changed_interfaces`, `graph_consumers_of`
  for tier promotion.

## Hookups

### 1. Spawn context (jig/runtime.py)

Replace "load every related artifact" with a graph-rooted neighborhood
walk. The spawn loader walks `TicketImpact.touched` plus depth-1
neighbors and resolves only those URIs. Cuts context by an order of
magnitude on big projects.

### 2. Reviewer dispatch (jig/reviewers/dispatch.py)

Selection becomes graph-aware:
- Touches an `exposed_api` / `emitted_event` with consumers →
  architectural + contract-compliance.
- Touches a node attached to a `tracer` → tracer-preservation
  reviewer (new, light: re-runs the bones smoke for that capability).
- Touches a `data_contract` with `dependent_contracts` (via Risk) →
  sa-tier reviewer.

### 3. Planner tier promotion (jig/planner_pm_mcp.py)

At ticket materialisation, compute `crossed_boundaries`. Above
configurable thresholds, auto-promote `dev_tier` (`standard → senior →
sa`) and post a thread Note so the operator can override.

### 4. Analytics

Emit a `TicketGraphImpact` event at spawn time with the impact
summary. Powers a quartermaster pattern check
("tickets-crossing-many-boundaries").

## CLI

```
jig graph build               # rebuild master + all per-ticket views
jig graph impact <ticket-id>  # print TicketImpact (json or pretty)
jig graph neighbors <node-id> [--depth N]
jig graph consumers <node-id>
jig graph tracers <node-id>
```

## File layout

```
.jig/graph/
  dependency-graph.yaml          # master snapshot
  ticket-impact/
    <ticket-id>.yaml             # per-ticket projection
```

Both files are derived; operators don't hand-edit.

## Implementation phases

| #  | PR                               | Surface                                                                                       |
|----|----------------------------------|-----------------------------------------------------------------------------------------------|
| 1  | Schema additions                 | `Module.consumes_*`, `Ticket.touches`, `ExposedAPI.kind` extension, cross-ref validation      |
| 2  | Types + derivation               | `DependencyGraph`, `Node`, `Edge`, `TicketImpact`, `jig/graph/derive.py`, fixture-based tests |
| 3  | CLI + MCP tools                  | `jig graph *`, `jig/graph_mcp.py`, wiring into `mcp_server.py`                                |
| 4  | Spawn-context narrowing          | Graph-neighborhood loader behind a feature flag for before/after comparison                   |
| 5  | Reviewer dispatch + tier promote | Graph-aware selection, tier-promotion heuristic                                               |
| 6  | Analytics + quartermaster        | `TicketGraphImpact` event, complex-ticket pattern check                                       |

PR #1 is purely additive schema work — safe to land first regardless
of the open questions below.

## Open design choices

These are decisions worth making before PR #2:

1. **Materialised vs. derived-on-demand.**
   Recommend materialised: reviewers, analytics, and operator UX all
   consume the same coherent snapshot the spawn used. Rebuild is
   seconds for projects we target. Pure-derived means every consumer
   rebuilds from scratch.

2. **`Ticket.touches` — required or optional?**
   Recommend optional with a reviewer flag: tickets with
   `dev_tier == "sa"` and empty `touches` are a code smell, but the
   lazy default (derive from `module_id + capability_ids`) handles
   ~70% of cases. Forcing every ticket to declare touches feels like
   busywork.

3. **Routes / migrations / env vars.**
   Recommend reusing `ContractsFile.exposes` with new `kind` values
   (`migration`, `env_var`) instead of inventing a separate
   "interface registry" artifact now. Cheap, declarative, and
   doesn't preclude promoting them later.

4. **Tracer identity.**
   Bones-layer tickets are the natural seed, but they get merged
   and forgotten as the project ages. Recommend a separate
   `.jig/spec/tracers/<id>.yaml` schema decoupled from ticket
   lifecycle. Small (id, name, exercises_node_ids, last_green_at,
   last_green_commit). Lets the tracer-preservation reviewer
   actually run something.

5. **Assumption nodes.**
   Doc 1 #11 wants an assumption ledger. `Risk` covers most of this
   surface. Recommend the graph treats `Risk` as the assumption node
   type for MVP. Introduce a separate `Assumption` schema only if
   authors find Risk too heavy.

## What this enables that we don't have today

- Reviewer federation knows "tracer X is exercised by N modules and
  ticket Y just touched one of them" → block on tracer rerun.
- Spawn context shrinks from "everything related" to "your
  neighborhood" → cheaper, more focused agents.
- Planner sees "this ticket touches 8 modules" → auto-promote tier.
- Operator gets `jig graph impact <id>` for what a ticket reaches into.
- Reviewers (architectural, contract-compliance, intent, etc.) all
  draw from one shared graph snapshot instead of rebuilding their
  own ad-hoc views.
