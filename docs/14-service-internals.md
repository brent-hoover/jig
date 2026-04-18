# 14 — Service Internals

The operational mechanics of the service: how agents get spawned, how
the scheduler picks what runs next, how the sandbox talks to the
service, how failures are recovered. Mostly mechanical decisions that
don't individually warrant their own records but are real design work
that affects what the harness can do reliably.

## Agent lifecycle

### Spawn

When the orchestrator decides to start an agent instance, it:

1. Resolves the role template and any phase-level overrides.
2. Resolves the context bundle, snapshotting URI references to specific
   versions.
3. Mints an instance-scoped auth token tied to (work-unit, role,
   parent-dev).
4. Allocates a sandbox container (Docker + bubblewrap).
5. Mounts the working-copy filesystem with scope appropriate to the
   role's allowed paths.
6. Starts the Claude Code process inside the sandbox with the prepared
   context and token.
7. Records the spawn event in the work unit's audit trail.

Spawn is the most mechanically involved operation in the service. It
touches policy (context bundle resolution), identity (token), isolation
(sandbox), and filesystem (mount). Failure at any step aborts cleanly
and records why.

Spawn failures to watch for:

- Required context URIs that don't resolve. Reports which URI failed;
  doesn't start the agent.
- Sandbox allocation failure (resource exhaustion). Queues retry or
  escalates.
- Token minting failure (auth backend down). Service-level error.

### Heartbeats

Each running agent emits heartbeats to the service over the WebSocket
connection. Heartbeat cadence:

- Default: every 30 seconds.
- Carries: instance ID, current phase, rough activity marker (last tool
  call timestamp, last message timestamp).
- Purpose: dead-agent detection, visibility.

Missed heartbeats:

- 2 missed (60s): soft warning, no action.
- 5 missed (150s): the service considers the instance suspect. Asks the
  container runtime whether the process is alive.
- Process dead per runtime: the service marks the instance as
  terminated, triggers cleanup.
- Process alive but silent: escalation — potentially stuck agent; the
  dev is notified.

Heartbeat is fire-and-forget over the existing WS connection. It does
not require a dedicated channel.

### Log streaming

Agent output (stdout, stderr, tool-call records, responses) streams
to the service in real time. The service:

- Persists raw output to per-instance log files (for audit and debug).
- Parses tool-call structure into events (which feed checkpoints and
  thread entries when agents explicitly post them).
- Forwards events to subscribed clients (TUI, web, other tools) via
  the WS event channel.

Log storage is bounded. Active instances keep logs accessible
immediately; closed instances' logs compress and archive. Old logs
prune per retention policy (default 90 days; configurable).

### Termination

Four termination paths:

**Normal completion.** Agent posts Handoff (or Checkpoint for
mid-phase), exits cleanly. Service cleans up: sandbox torn down,
resources released, instance state marked completed.

**Budget exhaustion.** Token limit reached. Service signals the agent
("wrap up — you're nearly out"), gives it a short window to post a
final Checkpoint, then terminates the sandbox. See [09](./09-checkpoints.md).

**Timeout.** Phase-declared max duration exceeded. Similar path to
budget exhaustion.

**Explicit cancellation.** Dev or orchestrator decides to stop.
Similar path; agent gets a chance to checkpoint, then sandbox goes down.

**Crash.** Process dies without warning. Harness detects via missed
heartbeat + process-dead. No final checkpoint; the last emitted
checkpoint is what's preserved. Sandbox cleaned up.

In all cases, the instance's state is recorded: reason for
termination, final phase, last checkpoint, resource usage.

### Cleanup

When an instance terminates, the service:

- Tears down the sandbox container.
- Revokes the instance-scoped auth token.
- Flushes remaining log output.
- Marks the instance's state terminal.
- Notifies subscribers (TUI, web, etc.).
- Updates the work unit state (phase pending-evaluator, or failed, or
  ready-for-resumption).

Cleanup is idempotent. Running cleanup twice on the same instance is
a no-op for the already-cleaned resources.

## Scheduling

The orchestrator is a work queue with policy. Decisions it makes:

- Which phases become eligible when (driven by workflow + checks +
  evaluator decisions).
- Which eligible phases get agents spawned (when resources allow).
- How to prioritize when there are more eligible phases than capacity.

### Concurrency model

Two concurrency limits:

**Global.** The service has a total concurrent-agents limit. Default:
10 for solo deployment, configurable for team. Protects against
runaway resource usage.

**Per-dev.** Each dev has a concurrency limit for work they've
initiated or been assigned as evaluator. Default: 3. Prevents a
single dev from flooding the queue.

### Priority

Phases have priorities derived from:

- Work-unit priority (if set explicitly).
- Work-unit size — smaller work tends to prioritize higher (short
  feedback loops matter).
- Age — older eligible phases climb priority over time (prevents
  starvation).
- Workflow stage — later phases (review, validate) typically prioritize
  higher than earlier phases (reduce in-flight count).

Priority is a score, not a class. Computed when scheduling decisions
are made. Simple heuristic, tunable per project.

### Fair sharing

When multiple devs have work in the queue and global capacity is
constrained, the scheduler round-robins across devs rather than
exhausting capacity for one dev's work. Prevents one dev
monopolizing shared resources.

Configurable: some teams prefer FIFO; the scheduler supports that as
an alternative policy.

### Preemption

Not in v1. Running phases complete or are explicitly cancelled; they
don't get preempted by higher-priority arrivals. Preemption is a
later-stage refinement if queue dynamics require it.

### Scheduling loop

The orchestrator's scheduling loop:

1. Collect eligible phases (phases whose prerequisites are met and
   that aren't already assigned).
2. Score each by priority.
3. Filter by available capacity (global + per-dev).
4. Assign top-N to agent spawns.
5. Record assignments.
6. Spawn agents via the lifecycle mechanism.

Loop runs on phase state changes (something became eligible) and on
completions (capacity freed up). Not continuous polling — event-driven.

## Sandbox and service protocol

Sandbox agents reach the service for everything — context reads, tool
calls that touch state (post thread entry, post checkpoint), escalations.
The sandbox-to-service path is narrow and well-defined.

### Transport

Unix domain socket when service and sandbox are on the same host.
HTTPS when they're not. The agent's client library abstracts; agent
code doesn't care.

Default for v1: Unix socket for same-host (solo, small team
deployments), HTTPS+WS for network-separated (team deployments with
agents running on separate workers).

### API surface

The sandbox can reach only these endpoints:

- **Context reads.** Resolve URI, return artifact. Scoped to the
  instance's context bundle. Attempts to read outside the bundle
  (excluded URIs, URIs not in declared context) return not-authorized,
  logged for audit.
- **Thread operations.** Post entries of the types the role is
  authorized to post. Role declares which.
- **Checkpoint operations.** Post checkpoints; read prior checkpoints
  on the same phase for resumption.
- **Heartbeat.** Emit periodic liveness.
- **Capability introspection.** Return the instance's full capability
  set (tools, context, limits). Helps agents reason about their own
  scope.

The sandbox cannot reach:

- SCM APIs directly. SCM access goes through the service.
- Other work units' state.
- Project-level configuration beyond what's in the context bundle.
- Other agents' threads or state.

### Authentication

Instance-scoped tokens, minted at spawn. Token carries:

- Instance ID.
- Work unit reference.
- Role and phase.
- Expiry (bounded to phase resource limits).

Service validates on every request. Token revocation on termination
prevents lingering access.

Tokens don't leave the sandbox. Agents use them via the client library;
they're not exposed to the agent's reasoning surface.

## Deadlock timeouts

Per [08](./08-threads.md): deadlock handling on unresolved blocking
entries. Timeout values:

- **Default per-phase timeout for blocking entries:** 24 hours.
  Configurable per phase type.
- **Shortened for automated phases:** 1 hour (if a phase with
  automated-only evaluation is blocked that long, something's wrong).
- **Extended for human-dependent phases:** 72 hours for phases waiting
  on specific humans (sick leave, travel, etc.).
- **Override per work unit:** for urgent work, timeouts can be reduced;
  for large work, extended. Configured at work unit creation or
  adjusted mid-flight.

When a timeout fires, the orchestrator escalates per the workflow's
escalation configuration. Either reassigns to an alternate actor,
notifies a human, or marks the phase as stuck.

## Crash recovery

The service can crash. When it restarts:

1. Load persisted state (SQLite — see [11](./11-state-location.md)).
2. Reconcile with agent state: query container runtime for live
   sandboxes.
3. For live sandboxes: re-establish WS connection or terminate the
   sandbox (depending on whether the agent is recoverable or not).
4. For dead sandboxes whose instances weren't marked terminated:
   treat as crashes, mark terminated, surface to devs.
5. Resume scheduling loop.

Sandboxes that were mid-work at service crash are in an awkward
position: the agent may still be running but unable to reach the
service. Options:

- **Kill on reconnect failure.** Simplest; loses work in flight.
- **Resume on reconnect.** Agent's WS connection reconnects with
  last-seen sequence; service replays missed events per
  [12](./12-service-shape.md) protocol.
- **Checkpoint-based recovery.** Sandbox gets terminated; next instance
  resumes from the last checkpoint (see [09](./09-checkpoints.md)).

V1 default: checkpoint-based recovery. Treats service restart the
same as any other mid-phase interruption, which we've already
designed for. Simpler than live-session reconnection and reuses
existing mechanisms.

## Backlog and unassigned work units

Work units exist in states beyond "actively running phases." A unit
can be:

- **Draft** — created but not yet started. Spec phase hasn't begun.
- **Active** — running phases, agents working.
- **Waiting** — blocked on an evaluator, a dependency, or a scheduled
  time.
- **Done** — completed successfully, archived.
- **Abandoned** — explicitly closed without completion, with reason.

Draft work units form an implicit backlog. Work units promoted from
deferred items (see [09](./09-checkpoints.md)) enter in Draft state.

V1 UX: a simple list view showing Draft work units with size, title,
source (who created, or which deferral promoted them). Humans or the
orchestrator can start them. No complex prioritization in v1 — teams
work through the backlog in whatever order makes sense.

## Deployment shapes

The service runs:

- As a local binary for solo use. SQLite on disk, Unix sockets for
  agents, binds to localhost. Starts on demand.
- As a long-running service for teams. Containerized or systemd,
  SQLite or Postgres (see below), HTTPS for clients, network paths
  to agent workers.

Same binary, different configuration. Configuration file declares:
persistence backend, bind addresses, auth backend, agent worker pool
configuration, adapter credentials.

### Persistence backend

SQLite is sufficient for most cases — solo use, small team use. Single
file, transactional, inspectable.

Postgres is an option for:
- Larger teams with many concurrent work units.
- Deployments that want backup/replication features.
- Teams with existing Postgres operational expertise.

Same schema across both; the service abstracts. SQLite is default; opt
into Postgres via configuration.

### Agent worker pool

In solo and small-team deployments, the service and agents run on the
same host. The service spawns sandboxes locally.

For larger deployments, agents run on dedicated worker hosts. The
service schedules; workers execute. Communication over network. The
sandbox-to-service path uses HTTPS+WS with mutual TLS.

V1 default: same-host. Multi-worker support is designed in but not
necessarily production-hardened in v1.

### Ingress

For team deployments, the service sits behind a reverse proxy
(nginx/Caddy/Traefik). WS traffic requires appropriate proxy
configuration. Documented in deployment docs, not here.

## Auth backend abstraction

The service has an auth interface; implementations plug in:

- **None.** Solo deployments; no auth check.
- **Static token.** Single shared secret. Small teams, trust-based.
- **OIDC.** SSO integration for larger deployments.
- **Custom.** For environments with specific requirements.

Auth backend is decoupled from the service core. Adding new backends
doesn't require touching anything else. The abstraction exists from
v1 even if only the simple backends ship.

## Service deployment: solo vs team

Solo deployment is the dev's own machine:

- Starts on first use of the harness CLI.
- SQLite on disk.
- Sandboxes on the same host.
- No auth.
- Stops when the dev is done.

Team deployment is shared infrastructure:

- Always-running service.
- SQLite or Postgres.
- Worker pool for sandboxes (same-host v1, separate hosts later).
- OIDC or static token auth.
- Reverse proxy for ingress.
- Per-dev identity and per-work-unit permissions.

Same service code. Configuration differs. A team deployment is
functionally a solo deployment scaled up with real auth.

## Logging and observability

Three kinds of logging:

- **Service logs.** Structured JSON, stdout. Errors, warnings, state
  transitions, scheduling decisions. Aggregated by normal log
  infrastructure.
- **Agent logs.** Per-instance files. Raw agent output (tool calls,
  responses). Kept for audit.
- **Audit logs.** Append-only JSONL in the repo archive. Work unit
  lifecycle events, decisions, approvals, merges. What persists
  beyond service lifetime.

Observability beyond logs: Prometheus metrics for service internals
(scheduling queue depth, active agents, heartbeat health), a small
dashboard for service health. Not elaborate; enough to catch
operational issues.

## What this covers vs defers

V1 in scope:

- All of agent lifecycle.
- Scheduling with priority and per-dev concurrency.
- Sandbox protocol surface with Unix socket + HTTPS.
- Deadlock timeouts with configurable values.
- Crash recovery via checkpoint-based resumption.
- Backlog as a simple list view.
- Solo and team deployment shapes.
- Auth backend abstraction with simple implementations.
- SQLite persistence; Postgres option.

Deferred:

- Preemptive scheduling.
- Distributed sandbox workers at scale.
- Rich backlog prioritization.
- Advanced observability (tracing, detailed metrics).
- Hot configuration reload.
- Multi-tenant single service (still one project per service).
