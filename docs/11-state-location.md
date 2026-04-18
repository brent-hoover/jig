# 02 — State Location

## Decision

**Live operational state lives in the service, not the repo.** The repo holds
durable artifacts (workflow templates, policy, context bundles, decision
records, archived closed work units). The service owns live state (active
work units, agent heartbeats, thread comms, scheduling).

Option 1 from the discussion (service-owned state), with a hybrid flavor:
closed work units get archived back to the repo on completion.

## Why

Repo-local state with multiple devs doesn't work:

- Two orchestrators writing to shared JSONL files either collide on commits
  or require locking that reinvents a service poorly.
- Git pull latency makes real-time visibility impossible.
- Merge conflicts on state files drown real code commits.

Service-owned state gives clean team coordination, real-time visibility, and
a single source of truth. The cost is running a service — accepted.

## Storage

**SQLite for live operational state.** Not JSONL. JSONL with multiple writers
needs either a single writer (which is what the service is) or real locking,
at which point SQLite gives better guarantees for free. Single file, still
inspectable, transactions, concurrent readers, indexes.

**JSONL as archive/export format.** Append-only audit logs and the
work-unit-close archive path.

**Archival path.** When a work unit closes, its full record (thread, decisions,
artifacts, audit trail) serializes to JSONL in the repo under a known path.
The service performs this commit under its own git identity.

## Boundary: what lives where

**In the repo:**
- Workflow template definitions.
- Role/agent template definitions (YAML).
- Policy definitions.
- Project-level context bundles.
- Archived closed work units (JSONL).
- Decision records (standalone artifacts).

**In the service:**
- Live work units and their state.
- Active agent instances, heartbeats, logs.
- Thread entries (until work unit closes).
- Scheduler/queue state.
- Short-lived auth tokens.

## Implications

- The service is the system. CLI, TUI, web view, agent wrappers are all
  clients.
- Service needs its own persistence, crash recovery, and audit log.
- Service needs git write access under its own identity to the archive path.
- If the service is down, nothing works. Accepted tradeoff.
