# 03 — Service Shape and Protocol

## Decisions

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

## Protocol: WebSockets

Agents need to push mid-execution — thread entries, escalations, state
changes. Polling is wasteful and laggy. WebSockets from the start.

### Logical channels on one socket

- **Command channel** — RPC-style: client asks service to do something
  (spawn agent, create work unit, post thread entry, resolve entry).
- **Event channel** — pub/sub: service pushes updates to subscribers
  (new thread entry, work-unit state change, agent heartbeat, completion
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
2. **Domain core** — work units, threads, workflows, policy evaluation,
   scheduling. Pure logic, no I/O.
3. **Persistence** — SQLite for live state, repo-writer for archival.
4. **Agent lifecycle** — spawning sandboxed Claude Code processes, tracking
   them, collecting output, enforcing scope. Most OS-level and likely the
   source of most weird bugs; worth treating as its own concern.

## Auth model

Two-layer identity (see [06](./06-agent-identity.md)):

- **Human identity** — real auth in team mode. Static token OK for solo.
- **Agent instance identity** — service-issued scoped token at spawn time,
  tied to (work-unit, role, parent-dev). Ephemeral.

Auth abstraction designed in from the start so adding OIDC/SSO later is
additive rather than invasive.

## Sandbox ↔ service communication

Agents run in Docker + bubblewrap sandboxes. The sandbox needs a narrow
allowlisted network path to the service API and nothing else. Unix socket
mount is a reasonable alternative for same-host setups.

This is a real design constraint: sandbox restricts the agent from hurting
the host, but the agent still needs to post thread entries, update state,
and escalate. The allowlist shape matters.
