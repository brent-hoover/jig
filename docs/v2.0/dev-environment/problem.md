---
title: Dev Environment & Service Isolation — Problem Statement
type: problem
status: draft
owner: brent
created: 2026-05-01
---

# Dev Environment & Service Isolation — Problem Statement

## Context

Jig today has two isolation primitives for agents:

- **Bubblewrap (bwrap)** — per-agent filesystem isolation; each agent sees a read-only root with its worktree mounted
  writable at `/workspace`.
- **Docker** — outer host isolation; the daemon and agents run inside a container so they can't reach the operator's
  machine outside what's mounted.

These cover *code* and *filesystem* isolation. They do not cover *runtime services*. When the SA contracts call for
Postgres + a message bus + Redis (etc.), there's no story today for how those services exist, how agents reach them, or
how two agents working concurrently avoid stepping on each other's data.

This is the missing third leg of the isolation stack.

## Problem

If the SA's `architecture.yaml` says "use Postgres," then dev agents implementing tickets need a Postgres they can write
to. The operator can't reasonably hand-install and hand-configure these services per project — that defeats the
"AI-driven" framing — and even if they did, parallel dispatch (federated reviewers, adversarial pairing, multiple
tickets at once) would silently corrupt shared state.

Concretely: two dev agents implement different tickets in the catalog suite simultaneously. Both write to the `products`
table. Without isolation:

- Agent A's test setup truncates the table; Agent B's tests fail mid-run for reasons unrelated to its work.
- Agent A's bad migration corrupts schema; Agent B never knows.
- Agent A leaves test fixtures around; Agent B's tests pass for the wrong reason.
- The reviewer agents see the wrong state when they verify.

The result: tests pass that shouldn't, tests fail that shouldn't, and the actual code quality is invisible behind the
noise. Tenet 1 collapses — coherence under decomposition is impossible if the agents can't even tell what their own work
did.

## Simplest possible solution

**One shared service per project, single-agent dispatch (one ticket at a time).** No isolation needed because there's no
concurrency. Operator runs `docker-compose up` once at project start; the orchestrator dispatches tickets serially
against those services; each agent's tests run in a fresh schema/topic the agent creates and tears down.

That works for v2 of jig with one operator running modest projects. It also genuinely matches what a single dev does on
their own machine — one schema, one bus, sequential work.

It does NOT support parallel dispatch. The moment the orchestrator wants to run two agents at once (federated review,
tracer-bullet of multiple modules in parallel, adversarial pairing), the simplest solution breaks.

## Complications considered

- **Scale**: at a single-operator, single-project scope, shared services hold up fine until concurrency is introduced.
  Forces: namespace-isolation primitive (per-agent schema/topic prefix) when parallel dispatch enters scope. N/A as long
  as single-agent dispatch is the v2 stance.
- **Concurrency**: this is the load-bearing complication. The moment two agents touch shared services simultaneously,
  state corruption becomes invisible and tests become unreliable. Forces: per-agent namespacing (Postgres schema, topic
  prefix, key prefix), or per-agent ephemeral instances for services that don't namespace. **The agent-leverage and PM
  workflow designs both assume parallel dispatch is the goal — so concurrency is not deferrable in v2.**
- **Failure modes**: namespace cleanup fails → resource leak (orphan schemas accumulate). Service crashes mid-agent →
  agent's test results unreliable. Forces: cleanup-on-completion (with debug-keep override), health-check at spawn,
  structured failure events. Tolerable because failure is observable; not silent.
- **Cross-cutting policies**: PII-encrypted-at-rest must apply to dev services too — agents don't get to write
  unencrypted PII just because it's dev. Forces: dev provisioning honors all cross-cutting policies; encryption keys per
  project, not per agent. Auth/secrets policies likewise apply.

Other complications worth naming:

- **Cost (resources)**: shared instances are cheap; per-agent ephemeral is expensive (N services × M agents). Forces
  default to shared+namespace; ephemeral only when service doesn't namespace cleanly.
- **Cleanup discipline**: orphan resources accumulate over project lifetime. Forces: explicit cleanup hooks with
  metrics; periodic sweeper for namespaces older than X without active agents.
- **External dependencies (Shopify, SEORank, etc.)**: real API calls during dev are slow, costly, and subject to rate
  limits the agent can't see. Forces: fixture-recording (vcr.py-style) by default; real calls only at tracer-bullet /
  final layer with explicit opt-in.

## Constraints

- **Must compose with existing isolation primitives.** Bwrap (filesystem) and Docker (host) stay; service isolation
  joins them as a third layer, not a replacement.
- **AI-driven configuration.** Operator doesn't hand-write docker-compose files. The dev environment is *derived* from
  `architecture.yaml`'s data_stores and external_dependencies declarations.
- **Must respect cross-cutting policies.** Dev services aren't a free pass on PII encryption, secret handling, audit
  logging.
- **Scale-down.** A trivial project (single SQLite file, no message bus) shouldn't trigger any of this — the simplest
  service is "no service" and that's fine.
- **Must be debuggable.** When an agent fails, the operator needs to be able to inspect what the agent's namespace
  looked like at the moment of failure. Cleanup-on-success but cleanup-on-failure-only-with-confirmation.

## Requirements

- A **dev environment manifest** derived from `architecture.yaml`. Tells the orchestrator what services to ensure are
  running and how to namespace per agent.
- A **provisioning step** in the agent spawn lifecycle: before bwrap fires, the orchestrator creates the agent's service
  namespace(s) and injects connection strings as env vars.
- A **cleanup step** in the agent completion lifecycle: on success, drop namespaces; on failure, archive for inspection
  (with operator-controlled retention).
- A **per-data-store provisioning strategy** declared in `architecture.yaml`: shared+namespace vs per-agent ephemeral vs
  operator-supplied (existing service the operator owns).
- An **isolation guarantee** per strategy: schema-namespaced services give strong data isolation; topic-prefixed brokers
  give strong message isolation but weak resource isolation (consumer groups, throughput).
- **External-API recording** as a default for development, with opt-in to real calls at appropriate phases (tracer
  bullet, final).
- **A health-check / availability check** at agent spawn: if the required services aren't reachable, the agent fails
  fast with a clear error rather than running and producing garbage.

## Non-goals

- Production deployment of these services. The dev environment is for *agents implementing the project*; deploying the
  resulting product is downstream and out of scope.
- Replacing the operator's existing local-dev workflow when not using jig. They can still run their own Postgres
  separately for non-jig work.
- Multi-machine / multi-host service orchestration. Single-host (one Docker host, one operator's laptop or one shared
  dev box) is the assumption.
- Cross-project service sharing. Each jig project has its own dev environment; namespaces don't span projects.
- Performance testing infrastructure. Dev services are sized for correctness, not load.
- Replacing the SA's contract authoring with auto-generated configs from real running services (reverse-engineering is
  its own problem).

## Success criteria

- An operator runs `jig start` (or equivalent) on a project whose `architecture.yaml` declares Postgres + NATS + S3. The
  dev environment comes up automatically, derived from those declarations.
- Two agents can be dispatched concurrently to tickets in the same suite without their tests interfering.
- An agent that fails leaves enough state behind for the operator to inspect what went wrong, then can be explicitly
  cleaned up via slash command.
- A trivial project (one SQLite file, no message bus) has zero dev-environment ceremony — the agent just runs.
- Cross-cutting policies (PII encryption, secrets via env, audit logging) hold in the dev environment same as
  production. No "dev exemptions."

## Open questions

- [ ] **External API recording mechanism.** vcr.py-style cassettes? Mitmproxy? Custom adapter per service? Probably
  per-service (Shopify lib has its own mock support; generic HTTP gets vcr).
- [ ] **Schema migration story.** When SA amends a contract that requires a schema change, how do existing agent
  namespaces get the migration? Re-create per agent? Apply to template schema then per-namespace re-apply?
- [ ] **Test data seeding.** Each agent needs realistic data to test against. Per-agent seed scripts? Shared seed
  applied to namespace template? Operator-provided fixtures?
- [ ] **Service version pinning.** The SA says "Postgres" — which version? Inferred from ecosystem defaults?
  Operator-pinned? Implied by the project's existing infra?
- [ ] **Operator-supplied existing services.** What if the operator already has a Postgres running they want to use
  instead of jig spinning one up? Need an "external" provisioning strategy that takes connection strings as input rather
  than launching a service.

## Change log

- 2026-05-01: Initial capture (brent + claude). Names service isolation as the missing third leg of the existing
  bwrap+Docker isolation stack. Frames the simple-vs-complex tradeoff (single-agent dispatch is simple but blocks
  parallelism). Establishes the requirement that this is not deferrable for v2 because parallel dispatch is assumed
  across multiple other v2 designs.
