# `jig.uri` — multi-authority `project://` URIs

Surface for parsing, resolving, and constructing `project://<authority>/<path>[@revision:N][#fragment]` URIs. See
`docs/v2.0/uri-scheme/design.md` for the full design.

## Submodules

| Module | Role |
|--------|------|
| `parser.py` | Pure-string parser — `parse_project_uri(s) -> ProjectUri` |
| `errors.py` | Error types: `ProjectUriError`, `UnknownAuthorityError`, `UnimplementedAuthorityError` |
| `resolver.py` | Multi-authority dispatcher — `resolve_project_uri(s, project_root, *, cache=None)` |
| `cache.py` | Per-orchestrator-session cache + event-driven invalidation (`UriResolverCache`) |
| `spec.py` | Spec-authority resolver (over `StructuredSpec`) |
| `arch.py` / `design.py` / `plan.py` / `store.py` | Stub authority resolvers |
| `constructors/` | Programmatic URI constructors per artifact kind |

## Programmatic URI construction

Agents and code build URIs via typed helpers, never string-templates. The constructors live under
`jig.uri.constructors` and re-export from one flat namespace.

### Naming convention

`<authority>_<artifact>_uri` — e.g. `spec_capability_uri`, `arch_module_uri`, `store_ticket_uri`. The flat surface plus
the authority prefix means an editor's autocomplete on `arch_` shows you everything in the `arch` authority at once.

### Files by authority

```
jig/uri/constructors/
├── __init__.py   — re-exports every constructor
├── _segments.py  — kebab-case segment + revision/fragment helpers
├── spec.py       — project / discovery / personas / journeys / playbacks / ontology /
│                   suites / capabilities / behaviors / acs / project_structured
├── arch.py       — architecture / modules / contracts / shared / integration_ac /
│                   behavioral / data / risks
├── design.py     — wireframes / system tokens / system components / system brand / frontend
├── plan.py       — build / epics / layers / tickets / deferred
└── store.py      — tickets / threads / thread entries / events
```

### When to use which

- Building a URI to put in an artifact field (a `payload_ref`, a `dependent_contracts` entry, a ticket
  `derived_from`): use the constructor. The constructor validates segments against kebab-case and ensures the
  shape matches the authority's grammar.
- Receiving a URI from operator input or another artifact: use `parse_project_uri(s)` to get a typed
  `ProjectUri`, then operate on its fields.
- Resolving a URI to its loaded artifact: `resolve_project_uri(s, project_root, cache=...)` — pass a
  `UriResolverCache` to opt into caching; omit it for one-shot calls.

### Design notes (from `docs/v2.0/uri-scheme/design.md`)

- **Caching is opt-in** via the `cache=` kwarg. Default behaviour is unchanged for callers that don't pass one.
- **Cache is event-driven** — `cache.subscribe_to_events(emitter)` wires invalidation to `ContractAmended` /
  `WireframeRevised` / `TicketStateChanged` events. TTL fallback (5 min) bounds worst-case staleness.
- **Constructors return strings**, not `ProjectUri` instances — callers wanting a parsed form do
  `parse_project_uri(s)` themselves.
