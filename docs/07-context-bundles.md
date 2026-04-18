# 07 — Context Bundles

The mechanism by which agents arrive at a work unit already educated. The
intervention for problems 3, 4, and 6: no thrashing on convention
discovery, consistent memory across agents on the same project, big-picture
context that lets agents make smarter autonomous decisions.

## Premise

Agents should not discover project context — they should receive it. Every
minute an agent spends grep'ing the codebase to figure out conventions,
opening unrelated files to understand the domain, or reading git history to
find precedent is a minute of wasted tokens producing worse output than if
the context had been handed over at spawn.

The harness's job: make context assembly a first-class operation. Define
what context exists, where it lives, how it's scoped, how it's assembled
into a bundle at spawn time, and how it's delivered to the agent.

## Three scopes

**Project scope.** Things true of the whole codebase, stable across work
units. Architecture overview, domain glossary, coding conventions, policy
definitions, established patterns, decision records from past work.
Changes slowly. Shared across all agents and work units in the project.

**Role scope.** Things a role needs regardless of which work unit they're
on. A reviewer's checklist. An implementer's guidance on what good
implementation looks like in this project. A planner's framework for spec
documents. Changes rarely. Shared across all agents filling that role.

**Work-unit scope.** Things specific to this task. The design doc produced
by the spec phase. The tests produced by the test phase. Prior thread
entries. Anything the decomposing parent passed in. Changes continuously
during the work unit's life. Private to this work unit.

Test for which scope something belongs to: if this changed, whose work
would be affected? Project-wide change → project scope. All future
reviewers → role scope. Just this task → work-unit scope.

## Curated, not discovered

The single most important property. The alternative — letting the agent
figure out what context it needs by exploring — is what produces the
thrashing.

Curated means: a human (or a context-curator role) decides what the
project-level bundle contains. That set is stable and visible. An agent
receives it; it doesn't go looking.

Cost: someone has to curate. Project context doesn't maintain itself. The
harness should make curation cheap but can't fully automate it. See
"context curation as a practice" below.

Benefit: agents start from a shared, intentional foundation. A reviewer
and an implementer working on the same work unit see the same project
context. Context quality becomes a team concern, not an agent concern.

## References, not copies

Bundle declarations are references to artifacts, not copies. A declaration
looks roughly like:

```yaml
required:
  - project://architecture
  - role://reviewer-checklist
  - workunit://design
optional:
  - project://domain-glossary
  - workunit://thread
```

The service resolves references at spawn time. Benefits:

- **Version consistency.** References are snapshotted to specific versions
  at spawn. If `project://conventions` changes mid-flight, the agent keeps
  what it had; the next spawn gets the new version.
- **Selective inclusion.** Not every agent needs every piece. A dev
  agent working on docs doesn't need full architecture; an infra agent
  doesn't need the domain glossary.
- **Auditability.** Bundle declarations are small and readable. You can
  see exactly what context an agent had without downloading it all.

## URI scheme

- `project://<path>` — curated project-level artifact. Lives in the repo
  under `.agents/context/project/` (or similar).
- `role://<role>/<path>` — role-level artifact. Repo-resident under
  `.agents/context/roles/<role>/`.
- `workunit://<artifact>` — work-unit-specific artifact. Resolved by the
  service from work-unit state: design, tests, thread, etc.
- `decision://<id>` — a specific decision record. Separate scheme because
  decisions are referenced across work units, not just within one.
- `repo://<path>` — a raw file in the repo, uninterpreted. Escape hatch
  for "the agent needs to see this specific file" without promoting it
  to a curated artifact.

The scheme is extensible. Teams can register custom resolvers
(`jira://<ticket>`, `wiki://<page>`) for project-specific external
sources. This is the plug point for context that doesn't live in the
repo — not a first-class scheme, an extensibility point.

## Required vs. optional

Required context that fails to resolve is a spawn failure. The agent
doesn't start. Better to fail loud at spawn than to let an agent proceed
without critical context and produce garbage.

Optional context that fails to resolve produces a warning and the agent
proceeds. Useful for things that exist on some work units but not others
(a thread doesn't exist until the first entry; a design doc doesn't exist
until the spec phase completes).

## Composition and layering

Context bundles compose across layers. For a specific agent spawn:

1. **Base context** — things every agent gets. Minimal; probably just a
   project overview and instructions on how to use the thread.
2. **Role context** — from the role template's default bundle.
3. **Phase context** — from the workflow phase declaration, which can add
   to or replace the role defaults.
4. **Work-unit context** — from the specific work unit's state.

Later layers can override earlier ones. A reviewer role defaults to
including the architecture doc; a specific phase of a specific workflow
might replace that with a targeted architecture-subsystem doc.

The bundle ultimately delivered to the agent is the composition result, not
four separate bundles. Declarations at each layer are additive unless
explicitly marked as replacing a prior reference.

## Delivery — three mechanisms, chosen per artifact

**Critical**: no single delivery mechanism works for all context. The
bundle declaration marks each reference with its delivery mode.

**System prompt injection.** Bundle contents rendered into the system
prompt before the agent starts. Agent sees it as part of its instructions.
Works for short, structured artifacts (conventions, checklists, policy
summaries). Token cost is fixed per spawn. Agent can't choose to skip it.

**Initial user message.** Bundle contents in a synthetic first user turn.
"Here's the context you need for this task: ..." Easier to structure as
discrete artifacts the agent can reference. Medium-sized artifacts
(design docs, thread summaries, decision records relevant to this work
unit). Slightly more token overhead than system prompt but clearer
structure.

**Read tools with curated paths.** The bundle exposes a virtual context
surface the agent reads through normal tools — `context_read(<ref>)` or a
read-only mount of bundle contents as files. Lazy loading; agent pays
only when it accesses. Large artifacts (full architecture docs, extensive
decision logs, the entire thread of a long-running work unit).

Defaults per URI scheme:

- `project://` — usually system prompt (short) or read tools (long).
- `role://` — usually system prompt.
- `workunit://design`, `workunit://tests` — usually initial user message.
- `workunit://thread` — initial user message for short threads, read
  tools for long.
- `decision://` — initial user message (relevant subset) or read tools
  (full log).
- `repo://` — always read tools.

Per-reference override is allowed; the defaults are just defaults.

## Thread as context

The work-unit thread is context for agents spawned into in-progress work.
A reviewer agent spawned after the implementer finishes sees the thread
including the implementer's handoff note ("look at xyz carefully
because abc"), all objections raised and resolved, all decisions recorded,
all notes about rejected approaches.

Two decisions:

**All entry types, not just gating ones.** Notes ("tried X, didn't work
because Y") are exactly the context that prevents repeated mistakes.
Including them is cheap and saves the agent from rediscovering.

**Full thread for v1; compaction deferred.** Long threads have token
cost. When cost becomes painful, add a thread-compaction mechanism (a
summary-entry type, produced by the harness or a dedicated agent). Not
in v1.

## Cross-instance continuity

When an agent instance ends and a new instance starts on the same work
unit, the new instance sees:

- The full thread (all Questions, Answers, Objections, Resolutions,
  Decisions, Handoffs, Notes from any prior actor on this work unit).
- Checkpoint state from the current phase (if resumption mid-phase) — see
  [09](./09-checkpoints.md).
- The same project and role context bundles as the prior instance had.

There is no separate memory layer beyond these. If the ending instance
knew something worth carrying forward, it had to externalize it — as a
thread entry, a checkpoint, a decision record, a deferred item, or a
handoff narrative. Anything it thought but didn't say is lost.

Two mechanisms serve two different continuity cases:

- **Normal phase transition** (implement complete, reviewer starting):
  Handoff entry on the thread. Artifacts + summary. No checkpoint state
  carries over — the prior phase's checkpoints are historical.
- **Mid-phase interruption** (implement crashed/killed/out-of-budget,
  next instance continuing the same phase): Checkpoints from the same
  phase. Continuous work-state snapshots, compacted as needed.

Why no hidden memory: this is problem 7 in another form. Letting
instances carry private internal state across sessions means bad
reasoning and wrong mental models can propagate invisibly. Forcing
externalization means everything the system "knows" is visible,
auditable, and challengeable. Same logic as "agents can't self-certify
completion."

Per-template memory (every dev-role instance shares a learned context
pool) is explicitly ruled out. Training data contamination,
nondeterminism, and debugging nightmares. Learning happens at the human
and curation layers, not in agent memory.

## Context curation as a first-class practice

Context doesn't maintain itself. The harness makes bad context obvious
but can't force good context to exist:

- **Missing required context fails spawn.** Can't ignore.
- **Staleness is visible.** Last-updated timestamps on project-level
  artifacts. If the architecture doc hasn't been touched in a year but
  the code has changed substantially, that's a signal.
- **Usage is observable.** The read-tool delivery mode tells you which
  artifacts agents actually access. Consistently unread artifacts are
  either wrong (not useful) or misplaced (wrong scope).
- **Curation is itself a work type.** Updating project context is a
  work unit with its own lightweight workflow. The same machinery that
  handles code changes handles context changes.

This is how context stays alive. Teams that don't treat curation as
ongoing work end up with rotted context and agents that behave as if
they have no context at all. The harness supports the practice; the
team has to actually do it.

## Agent instance memory (clarification)

"Per-agent memory" from problem 4 is addressed by the combination of
thread and checkpoints:

- **Within a single instance**: Claude Code's own session context. The
  harness doesn't add anything here.
- **Across instances in the same phase (mid-phase resumption)**: the
  checkpoint channel. See [09](./09-checkpoints.md).
- **Across phases in the same work unit**: the thread, including
  Handoff narrative summaries.
- **Across work units**: curated project-scope context, plus decision
  records referenced by `decision://` URIs.

No separate memory database. Everything persistent lives in the repo,
in work-unit state (thread + checkpoints), visible to anyone who looks.

## What this does for the original problems

- **Problem 3** (agents arrive undereducated): bundle declaration per
  role/phase means they arrive educated. Curated, not discovered.
- **Problem 4** (no consistent memory): thread-as-context for
  cross-instance continuity; curated project context for cross-work-unit
  continuity. No separate memory layer.
- **Problem 6** (no big-picture context): project scope + decision
  records. Decisions-with-rationale captured as Decision thread entries
  feed back into the project-scope bundle over time via the curation
  practice.

## Deliberately deferred

- **Thread compaction.** Not in v1. Add when token costs are painful.
- **Full custom URI resolver infrastructure.** The extensibility point
  exists in the scheme; the implementation can ship with just the
  built-in schemes and add custom resolver support when the first team
  actually needs it.
- **Smart context selection.** "Pick the relevant subset of project
  context for this work unit" — interesting but premature. Start with
  explicit declaration; observe what bundles teams actually want; then
  consider automation.
