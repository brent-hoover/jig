# 12 — Service Shape and Protocol

What the service is, where it keeps state, how clients talk to it.

## Decisions

- **Live operational state lives in the service; durable artifacts
  live in the repo.** Closed tickets archive back to the repo on
  completion.
- **SQLite for live state, JSONL for archives.**
- **Same binary, different config** for solo vs team deployments.
- **One service per repo.** No multi-repo multiplexing.
- **FastAPI async service, WebSockets for the client protocol.**
- **Auth abstraction from day one**, even if solo implementation is a no-op.

## Solo vs team

Same binary, different configuration:

- Solo: SQLite on local disk, no auth, binds to localhost, can auto-start
  when the CLI runs.
- Team: same binary, config points at a network interface, real auth, hosted
  somewhere reachable by the team.

No forked codebases. If it gets forked it will drift.

## One service per repo

Defaulting to per-repo isolation rather than multiplexing multiple repos into
a single service. Reasons:

- Simpler config, per-project policy, separate audit trails.
- Avoids cross-project visibility questions (does a reviewer on project A
  see project B's threads? sidestepped by not sharing).
- Clean operational boundaries.

## State location

Live operational state lives in the service, not the repo. The repo
holds durable artifacts (workflow templates, policy, context bundles,
decision records, archived closed tickets). The service owns live
state (active tickets, agent heartbeats, thread comms, scheduling).

Closed tickets archive back to the repo on completion, giving a
hybrid shape: service-owned while live, repo-owned once closed.

### Why not repo-local live state

Repo-local state with multiple devs doesn't work:

- Two orchestrators writing to shared JSONL files either collide on
  commits or require locking that reinvents a service poorly.
- Git pull latency makes real-time visibility impossible.
- Merge conflicts on state files drown real code commits.

Service-owned state gives clean team coordination, real-time
visibility, and a single source of truth. The cost is running a
service — accepted.

### Storage

**SQLite for live operational state.** Not JSONL. JSONL with multiple
writers needs either a single writer (which is what the service is) or
real locking, at which point SQLite gives better guarantees for free.
Single file, still inspectable, transactions, concurrent readers,
indexes.

**JSONL as archive/export format.** Append-only audit logs and the
ticket-close archive path per
[17](./17-directory-layout.md).

**Archival path.** When a ticket closes, its full record (thread,
decisions, artifacts, audit trail) serializes to JSONL in the repo
under a known path. The service performs this commit under its own
git identity.

### Boundary: what lives where

**In the repo:**

- Workflow template definitions.
- Role/agent template definitions (YAML).
- Policy definitions.
- Project-level context bundles.
- Archived closed tickets (JSONL).
- Decision records (standalone artifacts).

**In the service:**

- Live tickets and their state.
- Active agent instances, heartbeats, logs.
- Thread entries (until ticket closes).
- Scheduler/queue state.
- Short-lived auth tokens.

### Implications

- The service is the system. CLI, TUI, web view, agent wrappers are
  all clients.
- Service needs its own persistence, crash recovery, and audit log.
- Service needs git write access under its own identity to the archive
  path.
- If the service is down, nothing works. Accepted tradeoff.

## Protocol: WebSockets

Agents need to push mid-execution — thread entries, escalations, state
changes. Polling is wasteful and laggy. WebSockets from the start.

### Logical channels on one socket

- **Command channel** — RPC-style: client asks service to do something
  (spawn agent, create ticket, post thread entry, resolve entry).
- **Event channel** — pub/sub: service pushes updates to subscribers
  (new thread entry, ticket state change, agent heartbeat, completion
  events).

Same socket, logically separated.

### Resilience

**Replay missed events since sequence N.** Every event has a monotonic
sequence number per subscription scope. Clients reconnect with their
last-seen sequence; the service replays anything since. This makes the
system tolerate dropped connections, idle-timeout reverse proxies, and
network blips without special handling on the client.

This pattern has to be in the design from day one — bolting it on later
means rewriting client reconnect logic everywhere.

### Debugging

`curl` doesn't work for WS. A debug CLI speaking the protocol is a day-one
tool, not a nice-to-have. Also: log every message in both directions in
dev mode.

## Ingress (team deployments)

Reverse proxies (nginx, Caddy, Traefik) handle WS fine but may need explicit
config. Long-lived connections sometimes get dropped by intermediate
infrastructure; the replay-since-sequence pattern above is what makes that
a non-issue rather than a bug.

## Service internal layering

1. **API surface** — WS endpoints for clients. Thin, stateless, auth-checked.
2. **Domain core** — tickets, threads, workflows, policy evaluation,
   scheduling. Pure logic, no I/O.
3. **Persistence** — SQLite for live state, repo-writer for archival.
4. **Agent lifecycle** — spawning sandboxed Claude Code processes, tracking
   them, collecting output, enforcing scope. Most OS-level and likely the
   source of most weird bugs; worth treating as its own concern.

## Auth model

Two-layer identity (see [06](./06-agent-identity.md)):

- **Human identity** — real auth in team mode. Static token OK for solo.
- **Agent instance identity** — service-issued scoped token at spawn time,
  tied to (ticket, role, parent-dev). Ephemeral.

Auth abstraction designed in from the start so adding OIDC/SSO later is
additive rather than invasive.

## Sandbox ↔ service communication

Agents run in Docker + bubblewrap sandboxes. The sandbox needs a narrow
allowlisted network path to the service API and nothing else. Unix socket
mount is a reasonable alternative for same-host setups.

This is a real design constraint: sandbox restricts the agent from hurting
the host, but the agent still needs to post thread entries, update state,
and escalate. The allowlist shape matters.
