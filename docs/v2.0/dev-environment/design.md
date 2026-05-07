---
title: Dev Environment & Service Isolation — Design
type: design
status: draft
owner: brent
created: 2026-05-01
problem: ./problem.md
---

# Dev Environment & Service Isolation — Design

## Summary

The dev environment is **derived from `architecture.yaml`**, not authored separately. Each `data_store` and selected
`external_dependency` declares a `dev_provisioning` block that names a strategy: shared instance with per-agent
namespace (default), per-agent ephemeral instance, or operator-supplied. The orchestrator gains a provisioning step in
the agent spawn lifecycle that creates the agent's namespace(s), injects connection strings via env vars, and cleans up
at completion. Service isolation joins bwrap (filesystem) and Docker (host) as the third isolation layer.

External APIs are mocked via recorded fixtures by default; real calls land only at tracer-bullet / final layers with
explicit opt-in.

## Provisioning strategies

Three strategies cover virtually every case. The SA picks one per data store at architecture-authoring time; operator
can override.

| Strategy | When to pick | Isolation guarantee | Cost | Examples |
|---|---|---|---|---|
| **shared_with_namespace** (default) | Service has a namespace primitive (schema, prefix, bucket, key prefix) | Strong data isolation; weak global-resource isolation (consumer groups, throughput) | Cheap — one running service per project | Postgres (per-agent schema), NATS (per-agent topic prefix), Redis (per-agent key prefix), S3-compat (per-agent bucket prefix) |
| **per_agent_ephemeral** | Service doesn't namespace cleanly OR isolation requirement is absolute | Strong full isolation | Heavier — N services × M concurrent agents | SQLite (per-agent file), some single-leader databases, opinionated CLIs that own global state |
| **operator_supplied** | Operator has an existing service they want to use (legacy infra, dev-only sandbox they manage) | Whatever the operator's service provides; not jig's responsibility | Zero infra cost; operator-managed | Existing dev Postgres on operator's laptop; shared team dev cluster |

Default to `shared_with_namespace`. The SA picks `per_agent_ephemeral` only when there's a clear reason. The operator
overrides to `operator_supplied` when they want to keep using something they already manage.

## architecture.yaml — extensions

Each `data_store` (and applicable `external_dependency`) gains a `dev_provisioning` block:

```yaml
data_stores:
  - id: main-db
    kind: postgres
    rationale: ACID, mature, team familiarity
    accessed_by: [catalog-ingest, categorization, search-api]
    dev_provisioning:
      strategy: shared_with_namespace
      isolation: schema                          # postgres-specific: schema vs database vs row-level
      namespace_template: "agent_{ticket_id}"
      version: "16"                              # SA-pinned; defaults to ecosystem-current if omitted
      setup_hooks:
        - "apply migrations from sql/migrations/ at namespace creation"
      seed_hooks:
        - "load sql/seed-fixtures.sql (operator-provided)"
      cleanup_on_success: drop                   # drop | archive | keep
      cleanup_on_failure: archive                # archived to <project>/.jig/dev/archived/<ticket_id>/

  - id: event-bus
    kind: nats
    accessed_by: [catalog-ingest, categorization, analytics]
    dev_provisioning:
      strategy: shared_with_namespace
      isolation: subject_prefix
      namespace_template: "agent.{ticket_id}.>"
      version: "2.10"

  - id: search-index
    kind: opensearch
    accessed_by: [search-api]
    dev_provisioning:
      strategy: shared_with_namespace
      isolation: index_prefix
      namespace_template: "agent_{ticket_id}_"

  - id: agent-scratch
    kind: sqlite
    accessed_by: [some-module]
    dev_provisioning:
      strategy: per_agent_ephemeral             # SQLite doesn't share well across processes
      file_template: "/workspace/.dev/{ticket_id}.db"
```

External dependencies that are genuinely external (Shopify, SEORank) get their own provisioning block focused on
mocking:

```yaml
external_dependencies:
  - id: shopify-api
    kind: external_http
    base_url: "https://{shop}.myshopify.com/admin/api"
    dev_provisioning:
      strategy: recorded_fixtures
      fixture_dir: "tests/fixtures/shopify/"
      record_mode: replay_only                  # replay_only | record_new | record_overwrite
      real_calls_allowed_at:
        - tracer_bullet                          # bones layer can hit real API once to validate integration
        - final                                  # final layer when explicit operator opt-in
```

## The dev environment manifest (derived)

The orchestrator derives `.jig/dev/manifest.yaml` from `architecture.yaml` at project init / SA pass completion. The
manifest is the operational view: what services need to be running, what ports they're on, how the orchestrator talks to
them.

```yaml
# .jig/dev/manifest.yaml — generated, not hand-authored
generated_from: project://arch/architecture.yaml@revision:5
generated_at: 2026-05-01T18:00:00Z

services:
  - id: main-db
    image: postgres:16
    container_name: jig-{project}-postgres
    ports: [5432]
    health_check: "pg_isready -h localhost -p 5432"
    namespace_strategy: schema
    namespace_create: "CREATE SCHEMA IF NOT EXISTS {namespace};"
    namespace_drop: "DROP SCHEMA IF EXISTS {namespace} CASCADE;"
    namespace_archive: "ALTER SCHEMA {namespace} RENAME TO archived_{ticket_id}_{timestamp};"
    connection_string_template:
      "postgresql://jig:jig@localhost:5432/jigdev?options=-c%20search_path%3D{namespace}"

  - id: event-bus
    image: nats:2.10
    container_name: jig-{project}-nats
    ports: [4222]
    health_check: "nats account info"
    namespace_strategy: subject_prefix

  ... etc
```

A `docker-compose.yaml` is also derived (or the equivalent for Podman / Colima / etc.) so operators can `docker compose
up` the dev environment manually if they want to inspect or muck with it directly.

## Agent spawn lifecycle (with provisioning)

The orchestrator's existing agent spawn flow gains two new steps:

```
ticket dispatched
  ↓
1. RESOLVE         look up the ticket's module → find which data_stores it accesses (via architecture.yaml)
  ↓
2. PROVISION       for each accessed data_store with strategy=shared_with_namespace:
                     execute namespace_create with ticket_id substituted
                   for strategy=per_agent_ephemeral:
                     spawn the per-agent service into the agent's container
                   for strategy=operator_supplied:
                     verify the connection works; fail spawn if not
                   build the env-var injection map (DATABASE_URL=..., NATS_URL=..., etc.)
  ↓
3. HEALTH-CHECK    verify each provisioned namespace/service is reachable
                   FAIL FAST if not; agent never starts
  ↓
4. WORKTREE        existing — clone worktree, prepare /workspace
  ↓
5. SPAWN BWRAP     existing — start sandbox with worktree mounted
                   NEW: pass env-var injection map into bwrap's environment
  ↓
6. RUN AGENT       existing — spawn claude code subprocess
  ↓
                   (agent does its work, can connect to services via env vars)
  ↓
7. COMPLETION      existing — capture output, mark ticket
  ↓
8. CLEANUP         for each provisioned namespace/service:
                     on success → execute namespace_drop
                     on failure → execute namespace_archive (debug retention)
                     emit DevEnvironmentCleanup event
```

Steps 1-3 and 8 are new. Steps 4-7 are existing in jig today.

## Connection injection — env vars

Every namespace gets injected into the agent's environment as a connection string:

```bash
# Inside the agent's bwrap sandbox, derived per-agent:
DATABASE_URL=postgresql://jig:jig@host.docker.internal:5432/jigdev?options=-c%20search_path%3Dagent_t-001
NATS_URL=nats://host.docker.internal:4222
NATS_SUBJECT_PREFIX=agent.t-001
S3_ENDPOINT=http://host.docker.internal:9000
S3_BUCKET_PREFIX=agent-t-001-
SHOPIFY_FIXTURE_DIR=/workspace/tests/fixtures/shopify
SHOPIFY_RECORD_MODE=replay_only
```

The agent's code reads from env vars (which is the right pattern anyway per the `secrets-via-env` cross-cutting policy).
Behavioral contracts that say "use the catalog-client API not direct DB" are checked by the reviewer agent against the
actual code, not against connection-string presence.

## Service isolation — what each strategy actually guarantees

**`shared_with_namespace` — strong data isolation, weak resource isolation:**

- Postgres schema: agent A's `INSERT INTO products` lands in `agent_t-001.products`; agent B's lands in
  `agent_t-002.products`. Neither sees the other. ✓
- BUT both agents share the connection pool, the disk, the WAL, the planner cache. A bad query from agent A can starve
  agent B for I/O. Mitigation: monitor; this is a v2 concern unless it bites.
- NATS subject prefix: agent A's `PUBLISH agent.t-001.catalog.events` doesn't reach agent B's subscribers on
  `agent.t-002.catalog.events`. ✓
- BUT consumer groups / streams may have global resources (max-msgs, retention). Mitigation: per-agent stream configs,
  or accept the shared resource pool.

**`per_agent_ephemeral` — strong full isolation:**

- Each agent gets its own service instance running in its container. No shared state at all.
- Cost: N × M services running concurrently (N agents, M services per agent).
- Use only when shared+namespace can't deliver the isolation needed.

**`operator_supplied` — whatever the operator's service provides:**

- Jig doesn't manage isolation. If the operator points jig at their existing dev Postgres, they're responsible for
  whatever isolation that does or doesn't have.
- Useful when operators want to keep using their existing tools.

## External API mocking

Recorded fixtures (vcr.py-style) by default; real calls only at explicit phases.

```yaml
external_dependencies:
  - id: shopify-api
    dev_provisioning:
      strategy: recorded_fixtures
      fixture_dir: "tests/fixtures/shopify/"
      record_mode: replay_only
      real_calls_allowed_at: [tracer_bullet, final]
```

**Modes:**

- `replay_only` (default): fixtures are read-only; if the agent makes a request that doesn't match a recorded fixture,
  the request fails. Forces the agent to use what's recorded; surfaces gaps in the fixture corpus.
- `record_new`: missing fixtures get recorded against the real API; existing fixtures are replayed. Used during initial
  development to grow the fixture corpus.
- `record_overwrite`: every request hits the real API and overwrites the fixture. Used to refresh stale fixtures.

**`real_calls_allowed_at` controls when real calls escape the fixture layer:**

- `tracer_bullet` — bones-layer tickets can hit real APIs once to validate integration. Per-call rate-limit enforcement;
  failures get logged.
- `final` — final-layer tickets with explicit operator opt-in; for testing degraded-mode behavior, latency, etc.
- Otherwise: agents are blocked from making real external calls; they MUST use fixtures.

This default-to-mock posture protects against three common failure modes: rate-limit exhaustion from N agents hammering
an API, unintended writes to external systems during dev, and divergence between dev test results and real API behavior
(fixtures are versioned and the divergence is observable).

## Composition with existing isolation primitives

Service isolation joins the existing stack as the third layer:

```
┌──────────────────────────────────────────────────────────────┐
│  Layer 1: Docker (host isolation)                            │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  Layer 2: bwrap (per-agent filesystem isolation)       │  │
│  │  ┌──────────────────────────────────────────────────┐  │  │
│  │  │  Layer 3: service namespacing (NEW)               │  │  │
│  │  │                                                   │  │  │
│  │  │  Agent A: schema=agent_t-001, prefix=agent.t-001 │  │  │
│  │  │  Agent B: schema=agent_t-002, prefix=agent.t-002 │  │  │
│  │  │                                                   │  │  │
│  │  └──────────────────────────────────────────────────┘  │  │
│  └────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
```

All three are required for safe parallel dispatch. Docker keeps the host clean; bwrap keeps agents from seeing each
other's worktrees; service namespacing keeps them from seeing each other's data.

## Sequencing relative to jig v2

| Phase | What lands | What it enables |
|---|---|---|
| **v2 phase 1 (sequential dispatch)** | Provisioning hooks, single-agent default, manifest derivation | Reliable single-agent runs against derived dev services |
| **v2 phase 2 (when needed)** | Namespace isolation primitives for the common services (Postgres, NATS, Redis, S3-compat) | Two agents can run safely without collision |
| **v2 phase 3 (parallel dispatch)** | Orchestrator schedules N agents concurrently; provisioning + cleanup hooks fire per agent | Federated reviewer + adversarial pairing patterns actually run in parallel |
| **v2.x** | Per-agent ephemeral support; external-API fixture-recording infra | Edge cases and external-API testing |

The sequencing matters: parallel dispatch is assumed by the agent-leverage and PM workflow designs (federated reviewer,
adversarial pairing). Without service isolation landed, parallel dispatch produces silent corruption. So service
isolation is a hard prerequisite for those features even though they live in different design docs.

## Cleanup discipline

Cleanup is where this design most easily falls over. Three failure modes to design against:

**1. Cleanup hook fails silently → orphan resources accumulate.**

- Every cleanup attempt emits a `DevEnvironmentCleanup` event with success/failure.
- Failed cleanups are tracked in `.jig/dev/orphans.jsonl`.
- A periodic sweep (daily, or on `jig daemon start`) attempts to drop namespaces older than X days that aren't
  associated with active agents. Operator confirms before sweep actually runs (destructive default = ask).

**2. Agent crashes → cleanup never fires.**

- Agent-spawn registers the cleanup intent before the agent starts, in `.jig/dev/active.jsonl`.
- On daemon restart, the orchestrator reads `active.jsonl`; for any agent no longer running, fires the appropriate
  cleanup (drop on success-flagged, archive on failure-flagged or unknown).
- This is the same recovery pattern as the existing ticket-state recovery.

**3. Operator wants to inspect a failed agent's data → cleanup destroys evidence.**

- `cleanup_on_failure: archive` is the default. Failed agent's namespace gets renamed (preserved with prefix, not
  dropped).
- `/dev archive list` slash command shows preserved namespaces.
- `/dev archive drop <ticket_id>` to clean up after inspection is done.
- `/dev archive purge --older-than 30d` for routine maintenance.

## Risks (of this design)

- **Namespace template collisions.** Two ticket-ids that hash/sanitize to the same namespace. Mitigation: ticket-ids are
  UUIDs prefixed with `t-`; collisions extraordinarily unlikely; validate at provisioning time and fail fast.
- **`shared_with_namespace` doesn't isolate enough for some tests.** Some test patterns (DROP TABLE, ALTER ROLE) need
  more than schema isolation. Mitigation: those tests bump to `per_agent_ephemeral`; SA can override per data store.
- **Connection string injection leaks credentials.** Env vars are in the agent's process environment; if the agent dumps
  env, credentials leak to logs. Mitigation: dev credentials are generated per-project, not real; rotate freely; never
  use production credentials in dev.
- **Service-version drift.** SA pins `postgres: 16`; six months later 16 is EOL but no one re-pinned. Mitigation: `jig
  daemon start` checks pinned versions against an EOL warning list; surfaces upgrade nudge.
- **Operator-supplied services don't meet cross-cutting policies.** Operator points jig at a Postgres without
  encryption-at-rest; PII policy is silently violated. Mitigation: at provisioning time, query the service for
  policy-relevant config (e.g., `SHOW data_directory`); fail fast if policy violation detected. Hard mode: allow
  operator override with explicit `--unsafe` flag for known limitations.
- **Fixture corpus rots.** Recorded fixtures drift from real API behavior; tests pass but real calls would fail.
  Mitigation: periodic re-record against real API in a sandboxed CI run; fixture timestamps surfaced in TUI; alert when
  fixtures > 90 days old without re-record.
- **External-API real-call burst at tracer-bullet phase.** All bones tickets fire real API calls simultaneously, hitting
  rate limits. Mitigation: orchestrator-side rate limiter on real-call exits; serializes if needed.

## Out of scope

- Production deployment of these services. Dev environment ≠ production environment.
- Multi-host service orchestration (one operator's laptop only).
- Non-Docker / non-container service hosts (operator-supplied services are the escape hatch for this).
- Complex CI/CD integration. The dev manifest can be reused in CI but full CI design is separate.
- Service mesh, observability stack, distributed tracing infrastructure for the dev services themselves.
- Snapshotting / time-travel debugging of dev service state.

## Open questions

- [ ] **Migration story when SA amends a contract that requires a schema change.** Do existing agent namespaces get the
  migration applied? Re-create per agent? Apply to template then per-namespace re-apply? Probably: namespace template is
  versioned; new agents get latest; existing agents finish on their version; cleanup handles version reconciliation.
- [ ] **Test data seeding strategy.** Per-agent seed scripts run at namespace creation? Shared seed file? Operator-
  provided fixtures? Probably: per-data-store `seed_hooks` in dev_provisioning that run after namespace creation.
- [ ] **Service version pinning.** Defaults from where? Probably ecosystem-defaults (latest stable) unless SA pins
  explicitly. Operator override via dev_provisioning override file.
- [ ] **Operator-supplied external services.** What if operator already has a Postgres they want to use? Need an
  `external` provisioning strategy that takes connection strings as input rather than launching a service. (Sketched
  above as `operator_supplied`; details TBD.)
- [ ] **How does the operator inspect an active agent's namespace mid-run?** `psql jigdev` and `\dn agent_*` works for
  Postgres but is service-specific. TUI affordance? `/dev shell <ticket_id>`?
- [ ] **What about agents that don't need any of the SA's services?** PO discovery agents, SA agents themselves,
  reviewer agents — they read artifacts, don't write to data stores. Provisioning should skip them. The role config
  needs a `requires_dev_services: bool` flag; default true for dev/spike tickets, false for PO/SA/reviewer/PM.
- [ ] **Performance overhead.** Spawning per-agent namespace creation adds setup time. How much, and does it dominate
  small ticket runs? Probably cheap for schema/prefix; expensive for ephemeral. Measure once we have numbers.

## Implementation phases

1. **architecture.yaml schema extension** — `dev_provisioning` block on `data_stores` and applicable
   `external_dependencies`. Pydantic models. Default values for shared+namespace.
2. **Manifest derivation** — `.jig/dev/manifest.yaml` generated from architecture.yaml; `docker-compose.yaml` generated
   alongside for operator inspection.
3. **Provisioning hooks in orchestrator** — RESOLVE / PROVISION / HEALTH-CHECK steps in agent spawn; CLEANUP step in
   completion. Initial set: Postgres schema, NATS subject prefix, Redis key prefix, S3-compat bucket prefix, SQLite
   per-file.
4. **Connection injection into bwrap** — env-var map passed through to the sandbox.
5. **Cleanup with archive-on-failure** — `.jig/dev/active.jsonl` and `.jig/dev/orphans.jsonl` for recovery; periodic
   sweep with operator confirmation.
6. **External API fixture support** — recording mode, replay-only default, allowed-real-calls phase gating.
7. **TUI affordances** — `/dev manifest`, `/dev archive list`, `/dev archive drop`, `/dev shell` (when feasible).
8. **Per-agent ephemeral support** (later) — for services that don't namespace cleanly.
9. **Parallel-dispatch enable** (gates on isolation primitives proven in single-agent runs).

## Change log

- 2026-05-01: Initial design (brent + claude). Three provisioning strategies (shared+namespace default, per-agent
  ephemeral for non-namespaceable, operator-supplied for existing infra). Architecture.yaml extension with
  dev_provisioning blocks. Manifest derivation as the operational view. Orchestrator gains RESOLVE / PROVISION /
  HEALTH-CHECK / CLEANUP hooks in the spawn lifecycle. External APIs default to recorded fixtures with phase-gated
  real-call escape. Cleanup discipline (archive-on-failure, orphan tracking, periodic sweep with operator confirmation).
  Service isolation joins bwrap and Docker as the third isolation layer.
