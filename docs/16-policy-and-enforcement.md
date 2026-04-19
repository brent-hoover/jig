# 16 — Policy and Enforcement

The mechanism that makes the promise in [01 — Core concepts](./01-core-concepts.md)
real: one set of best practices, enforced identically for devs and
agents. One promise, three mechanisms — because the three kinds of
policy behave differently and need different enforcement surfaces.

## Premise

A team's engineering discipline lives in its rules: what actors are
allowed to do, what must happen before work advances, what the produced
artifacts must look like. Agents joining the team either inherit those
rules or erode them. There is no middle ground.

The previous docs named "policy" in several places without defining the
term precisely. Reading them together, policy covers three distinct
things that deserve separate treatment:

- What an actor *can do*.
- What *must happen* before work advances.
- What the *artifacts produced* must look like.

One blanket concept obscures that the three enforce at different
points, against different actors, via different mechanisms. This
document names them, says where each lives, and describes how
enforcement works.

## The three kinds of policy

**Capability policy.** What an actor can do. Tools, tool parameters,
paths, network destinations. Per-template-and-phase. Enforced at
execution time. Applies to agents only — a human's editor can do
whatever the human has OS permissions for.

**Process policy.** What must happen for work to advance. Required
phases, required evaluators, required checks, unresolved-blocker
gates. Already encoded by the workflow model ([05](./05-workflow-model.md))
and the verification layer ([10](./10-verification.md)). Applies
identically to agents and humans because both run through the same
workflow.

**Content policy.** What the artifacts produced must look like. Lint
passing, types checking, tests passing, security scans clean, coding
conventions followed. Enforced as checks at appropriate phases. Same
enforcement for humans via git hooks and CI.

"One policy, two enforcement points" from [01](./01-core-concepts.md)
is strictly true only for process and content policy. Capability
policy applies only to agents. Worth naming explicitly: the symmetry
isn't perfect, and nothing in the design depends on pretending it is.

## Where policy lives

No single `policy.yaml`. Each kind lives with its natural entity:

- **Capability policy** → role templates ([06](./06-agent-identity.md))
  with phase-level overrides ([05](./05-workflow-model.md)).
- **Process policy** → workflow definitions ([05](./05-workflow-model.md)).
- **Content policy** → the check catalog ([10](./10-verification.md)).

This doc is the central reference for how policy works; the content
itself lives with the entities it governs. Creating a separate
policy-repo artifact would duplicate what already exists and invite
drift.

## Capability policy

The mechanism that prevents agents from doing things they shouldn't —
the one that also prevents unauthorized edits to durable artifacts by
the wrong role. A dev agent shouldn't write to `.jig/spec/`; a spec
agent shouldn't write to `src/`; neither should run destructive shell
commands or push to remotes without authorization.

### Declaration

Capabilities are declared on the role template ([06](./06-agent-identity.md))
and can be overridden per phase in a workflow ([05](./05-workflow-model.md)).
Declarations express *intent*; the harness compiles intent to enforcement
configuration at spawn time.

Rough shape of a template's capability declaration:

```yaml
capabilities:
  tools:
    allowed: [Read, Edit, Write, Glob, Grep, Bash]
  tool_params:
    Bash:
      deny_patterns:
        - "rm -rf"
        - "git push"
        - "curl http[s]?://(?!localhost|127)"
  paths:
    writable:
      - "ticket://worktree/**"
    readable:
      - "repo://**"
    denied:
      - ".jig/spec/**"
      - ".jig/decisions/**"
      - ".jig/roles/**"
      - ".jig/workflows/**"
      - ".jig/checks.yaml"
```

Phase overrides layer on top. A review phase for the same role might
drop `Edit`/`Write` entirely; an integration phase for a parent
workflow might widen the writable paths.

### Enforcement split

Three enforcement mechanisms, each appropriate for a different
constraint category:

**Docker** — outer sandbox. Host isolation, network egress allowlist,
resource limits. Agent cannot reach the host filesystem, cannot reach
arbitrary network destinations, cannot exhaust host resources. Covers
blast radius beyond this ticket.

**bubblewrap** — inner sandbox. Filesystem scope. The working copy
mounts as writable at a known path; the rest of `/` mounts read-only.
Policy-level path constraints (denied, read-only) lower the bounds
further. Covers structural filesystem isolation.

**Claude Code hooks** — logical and semantic enforcement. Command
pattern filtering, parameter validation, fine-grained path checks
(denying `.jig/spec/**` even though bwrap would make it writable).
Covers rules that need to inspect tool invocations rather than
restrict filesystem access.

Each layer has its own appropriate role. Docker handles what Docker
handles well; bwrap handles what bwrap handles well; hooks handle
everything that needs semantic inspection of tool calls.

### Hook compilation at spawn time

Pre-spawn, file-based. The service compiles the effective capability
set (template + phase overrides + ticket context) into two files
materialized into the sandbox before the agent starts:

- `/jig/policy/rules.json` — the compiled ruleset for this spawn.
  Contains the expanded path globs, command pattern regexes, parameter
  constraints. Readable by the hook scripts.
- `.claude/settings.json` (inside the sandbox) — Claude Code's hook
  registration. Points each relevant tool event at a small hook
  script that loads `rules.json` and evaluates the event against it.

The hook scripts ship with the service and are bind-mounted at
`/jig/bin/`. Per-spawn, only `rules.json` and `.claude/settings.json`
are regenerated; the scripts are static.

Why file-based rather than passing context through environment
variables: rules can be large and nested, env vars are a flat
key-value namespace, and re-reading a JSON file per hook invocation
is cheap. Also keeps the compiled ruleset inspectable after the fact
— a crashed agent's sandbox can be opened and the rules read back.

### Worked example: `no-destructive-bash`

Template declares:

```yaml
capabilities:
  tool_params:
    Bash:
      deny_patterns:
        - "rm -rf"
        - "git push --force"
        - "git reset --hard origin"
```

Service compiles this into `rules.json`:

```json
{
  "bash": {
    "deny_patterns": [
      "rm\\s+-rf",
      "git\\s+push\\s+--force",
      "git\\s+reset\\s+--hard\\s+origin"
    ]
  }
}
```

And registers the hook in `.claude/settings.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {"type": "command", "command": "/jig/bin/check-bash"}
        ]
      }
    ]
  }
}
```

When the agent tries to run `rm -rf /workspace/build`, Claude Code
invokes `/jig/bin/check-bash` with the tool-call details on stdin.
The script reads `rules.json`, matches the command against the
deny-patterns, and exits with code 2 plus a stderr message:
`blocked by policy: destructive Bash command matches
deny-pattern "rm -rf"`. Claude Code presents this to the agent as a
tool failure; the agent adapts (posts a Question, attempts a
different approach, or escalates).

Path protection follows the same pattern. A `Write` call to
`.jig/spec/project.structured.yaml` from a dev role is checked by
`/jig/bin/check-path`, denied with a similar message.

### Runtime override: not supported

Capability policy is non-overridable at runtime in v0.2. If an agent
hits a deny, it cannot elevate itself to proceed. The options are:

- Adapt within current capabilities.
- Post a Question or Escalation asking a human to intervene.
- Post a Proposal to change the template's capabilities for future
  spawns ([04](./04-ownership.md)).
- Halt and wait for human intervention (human may promote the work
  unit to a different template with broader capabilities and resume).

Runtime override would defeat the sandbox — any mechanism an agent
can invoke to elevate itself is a mechanism that can be used
incorrectly. The escalation path keeps elevation visible and
human-authorized.

### Audit

Hooks enforce; they do not signal. Claude Code already streams tool
calls and their outcomes to the service via the log stream
([14](./14-service-internals.md)). Denials appear in that stream as
tool failures with the hook's stderr as the reason. The service's
audit log ingests this automatically — nothing extra to wire.

A separate signaling path (hook → service RPC) was considered and
rejected: adds network calls inside the sandbox, duplicates the log
stream's audit data, and multiplies failure modes. Enforcement-only
keeps hooks small, synchronous, and bound to the event they gate.

## Process policy

Already enforced by the workflow model ([05](./05-workflow-model.md))
and verification ([10](./10-verification.md)). This doc names the
concept; the mechanics live there.

For agents: the orchestrator is the enforcer. Phases become eligible
only when prerequisites are met. A completing actor posts a Handoff;
required checks run; evaluators accept or reject. Unresolved blocking
thread entries prevent advancement. Self-certification is structurally
impossible ([10](./10-verification.md) "Evaluators").

For humans: same workflow, same gates. A human claiming a phase goes
through the same lifecycle — posts a Handoff, waits for checks,
waits for evaluator acceptance. Human work does not get a side door
around process policy.

The one asymmetry worth naming: an agent that cannot satisfy process
policy typically escalates; a human that cannot satisfy it typically
either fixes the problem or waives with justification. Both paths
produce audit records; the mechanism is the same.

## Content policy

Already enforced by the check catalog ([10](./10-verification.md)).
This doc names the concept; the mechanics live there.

For agents: checks run as part of phase verification. A required
check failure blocks phase advancement and surfaces as a check-
failure thread entry the implementing actor must resolve.

For humans: the same check commands run pre-commit via git hooks
installed by the harness, and pre-merge via CI. Humans feel the
policy at the same points they already feel lint and tests — the
harness just ensures the rules are the ones the team committed to,
not each dev's local configuration.

Waivers apply to content policy, per [10](./10-verification.md).
SA waives technical checks; any dev can waive warnings (never
required-level).

## Policy ownership

All three kinds are SA-owned. This extends
[04 — Ownership](./04-ownership.md), which already declares
`security_policy: sa`. The full picture:

```yaml
ownership:
  # ... (existing entries)
  capability_policy: sa       # role templates, phase overrides
  process_policy: sa          # workflow definitions
  content_policy: sa          # check catalog
  security_policy: sa         # subset of capability + content
```

Capability policy touches security directly (what agents can do);
process and content policy touch quality and discipline. All three
are technical decisions, so SA is the right owner. PO owns product
shape, not how the team works.

Proposals to change policy route to SA per the standard mechanism
([04](./04-ownership.md)). A dev who hits a capability deny can
propose loosening it; SA decides.

## Policy versioning

Policy artifacts are files in the repo, versioned by git. In-flight
tickets keep the policy version they were spawned with — same
model as context bundles ([07](./07-context-bundles.md)). A
mid-flight policy change does not retroactively affect running
agents; the next spawn picks up the new version.

Rationale: an agent spawned under one ruleset completing under
another creates mid-flight failures that are hard to reason about.
Version snapshotting keeps enforcement deterministic per spawn.

## Human-side setup

On `jig init` (or equivalent setup command), the harness installs
git hooks into the repo's `.git/hooks/` directory. Hooks invoke the
same check commands declared in the content-policy catalog. A dev
committing locally gets immediate feedback matching what the harness
would run at phase verification.

Scope of harness-installed hooks:

- `pre-commit` — runs required content checks (lint, type-check, format).
- `pre-push` — optionally runs tests if configured.
- `commit-msg` — optionally validates commit format (conventional
  commits, etc.) if configured.

The harness does *not* install or manage CI. CI is the team's
infrastructure; the harness reads CI status via the SCM adapter
([13](./13-scm-integration.md)) and treats CI results as automated
checks feeding into verification. Teams without CI get a weaker
safety net on PRs from external contributors, but the harness doesn't
try to fill that gap.

Hook installation is idempotent and backs up any existing hooks
before writing. Teams with existing custom hooks get a merged
configuration or a conflict report, not silent overwrite.

## What this does for the original problems

- **Problem 7** (agents declare work done when it isn't): the
  combination of capability policy (can't write `.jig/spec/` to
  fake a spec), process policy (can't skip required phases or self-
  evaluate), and content policy (can't ship code that fails checks)
  closes the paths by which an agent could mark work done past
  objective gates.
- **Problem 2** (humans need to intervene in agent exchanges): the
  escalation path when an agent hits a capability deny is the clean
  injection point. The agent halts visibly; a human sees the
  escalation; resolution is tracked.
- **Problem 3** (agents arrive undereducated): capability policy
  makes the boundaries of agent authority explicit. An agent knows
  what it can and can't do at spawn because the rules are declared
  in its template, not discovered by trial and error.

## Deliberately deferred

- **Runtime capability elevation.** The escalation path is sufficient
  for v0.2. A first-class elevation protocol ("agent requests
  temporary capability X; human approves in-band") is a v2+ concern
  if the current escalation proves too coarse.
- **Policy testing framework.** "Does this capability rule do what I
  think it does?" — a test harness for policy declarations. Worth
  building once the ruleset is non-trivial; not v1.
- **Policy bypass analytics.** Dashboards showing where waivers
  cluster, which denies are most frequent, which templates generate
  the most escalations. Observability layer, deferred until
  operational data exists.
- **Cross-project policy sharing.** A policy library used across
  multiple projects. Out of scope — one project per service.
- **Dynamic policy (runtime-computed rules).** Policy is static per
  spawn in v0.2. Rules that depend on runtime state (e.g., "can
  write this path if the ticket is type X") are handled by
  compiling them at spawn time with the ticket context baked in —
  the dynamism lives at compile, not at enforcement.
- **Hook-based signaling.** Hooks enforce only. If richer
  signaling is ever needed (hook → service notifications beyond
  what the tool stream carries), the mechanism can be added later
  without changing the enforcement contract.
