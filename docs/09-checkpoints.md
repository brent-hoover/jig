# 09 — Checkpoints

A separate channel on each work unit, recording an agent's in-progress
state during a phase. The recovery mechanism for mid-work interruption,
and the scope-discipline mechanism for agent behavior.

## Why a separate channel from the thread

Threads are conversational: Questions, Answers, Objections, Decisions —
content that humans and agents exchange about the work. Checkpoints are
operational: an agent's own record of what it's doing and where it is,
for its own continuity.

Different audience (agent continuity, not team communication), different
lifecycle (ephemeral during success, critical during failure), different
retention (compacted and phase-pruned, not preserved indefinitely).
Putting checkpoints in the thread would add high-frequency noise to a
channel that's meant to be read by humans and other actors. Separate
channel keeps both clean.

Checkpoints remain visible to anyone who wants to look — they're not
hidden state. The separation is about organization, not access control.

## What a checkpoint contains

- **Completed.** What was just finished (concrete and recent).
- **Position.** Current state — file being edited, test being debugged.
- **Plan.** What the agent intends to do next.
- **Ruled out.** Approaches tried and rejected, with reasons.
- **Deferred items.** Things acknowledged as worth doing but explicitly
  put off, with reason. See below.
- **Open questions.** Things the agent is uncertain about but hasn't
  posted as thread Questions yet (not sure if they're real, or scoping
  the problem first).

## Cadence

**Harness-triggered (automatic):**
- After commit.
- After test run (pass or fail).
- After significant file writes completing a discrete edit.
- Before any phase-transition attempt.

**Agent-triggered (tool calls):**
- `checkpoint_decision(decision_ref, rationale)` — references a Decision
  entry posted to the thread; checkpoint captures the work-state context
  around the decision.
- `checkpoint_deferred(item, reason)` — explicitly defers an item.
- `checkpoint_milestone(description)` — general "good stopping point"
  marker.

Agent-triggered checkpoints are at the agent's discretion; the harness
doesn't wait for them. The automatic triggers are the baseline — no matter
what the agent does, work state is captured at meaningful operational
events.

## Deferred items — dual purpose

The `checkpoint_deferred` mechanism does two jobs.

**Recovery.** If the agent dies, the next agent sees what was deferred and
can decide to do it, continue deferring, or flag it.

**Behavioral shaping.** Without an outlet, an agent that notices something
worth doing has two options: do it now (scope creep, overengineering) or
drop it silently (information lost). With a deferral outlet, there's a
third option: acknowledge it, record it, move on. The agent gets
permission to say "noted, not now," which is exactly the discipline that
keeps scope under control.

This is the same dynamic as TODO comments, backlog tickets, and "out of
scope for this PR" notes in human engineering. The act of recording is
what frees the actor to stay focused.

The tool description for `checkpoint_deferred` should explicitly encourage
the behavior: "if you notice something worth doing that isn't strictly
required for the current phase, prefer deferring it over doing it." The
prompting matters as much as the mechanics.

## Lifecycle of deferred items

Deferred items don't just sit as notes. They have downstream consequences:

- **Finished in the same phase.** If the agent completes its core work
  with time/budget to spare, it may go back and do deferred items.
- **Evaluator reviews at handoff.** The completing handoff includes a
  list of items deferred during the phase. The evaluator decides whether
  any of them should have been done in-phase and either accepts or
  objects.
- **Promoted to a new work unit.** Real work that someone should do
  later becomes its own work unit. The evaluator (or the completing
  agent in its handoff) can elect to promote.
- **Explicitly accepted as deferred.** Recorded and acknowledged as
  genuinely out of scope; no follow-up action.

The service tracks deferred items across the work unit's life and
surfaces them at phase transitions. Handoff entries include the deferred
items list for evaluator review.

## Compaction

Keeping every checkpoint indefinitely isn't useful. The resuming agent
doesn't need every intermediate state; it needs enough to understand
trajectory without paying the token cost of every event along the way.

**During-phase compaction.** When checkpoint count or token cost crosses
a threshold, the harness (or a dedicated compactor agent) rolls older
checkpoints into a summary. "First 20 checkpoints → summary of schema
design, draft migration, initial test stubs. Current: debugging
timezone failure in test X." The resuming agent reads: compacted summary
+ most recent uncompacted checkpoints.

Compaction is discrete and event-triggered, not continuous. Original
checkpoints are archived, not deleted — audit retains full history.

**Phase-boundary pruning for resumption.** When a phase completes
successfully, its checkpoints aren't useful for resumption of
subsequent phases — the Handoff entry is the canonical record. Prior-
phase checkpoints are marked as historical. The resuming agent for a
new phase doesn't read them; the evaluator of the new phase doesn't
need them.

Phase checkpoints from a prior failed attempt (e.g., implement → review
→ implement loop) are also historical for the second attempt — the
second implement starts fresh and builds its own checkpoint stream.

**Cross-phase audit retention.** Historical checkpoints stay on the work
unit for retrospective viewing. "What did the first implement attempt
actually do before it got sent back?" is useful learning data.
Archived-with-work-unit at closure.

## Failure-to-progress detection

Your concern: agent 1 dies, agent 2 starts, also dies, infinite loop
making no forward progress.

Checkpoints make this detectable: **if an agent terminates and the most
recent checkpoint has not advanced past the checkpoint the terminating
agent started from, the phase has failed to progress.**

The harness treats this as an escalation signal. It does not spawn
another agent of the same template on the same starting state. Options:

- Escalate to a human.
- Try a different agent template (larger context window, different
  role).
- Mark the phase as stuck, pending human intervention.

"Budget exhaustion twice in a row without progress" is a fundamental
signal, not a reason to keep trying.

## Scope

Checkpoints are per-work-unit with phase markers. They persist for the
life of the work unit and archive with it at closure.

Checkpoint operations filter by phase: resumption reads only current-
phase checkpoints; audit views can filter by phase for retrospective
analysis; compaction operates within a phase.

## Relationship to threads

Checkpoints and thread entries share the principle of externalization:
everything the agent wants to persist past its own session has to be
externalized to one channel or the other. Nothing hidden carries across
instances.

Where entries go:

- Thread: Questions, Answers, Objections, Resolutions, Waivers,
  Decisions, Handoffs, Escalations, Uncertain, Notes. Conversational and
  gating.
- Checkpoints: current work state, decision references, deferred items,
  milestones. Operational and continuity-focused.

Cross-references are common — a Decision is posted as a thread entry
*and* referenced by a checkpoint so the resuming agent sees the decision
in the context of when during the work it was made.

## What this changes about "cross-instance continuity"

Earlier (07) said continuity goes through the thread alone. That was
wrong for the mid-phase-interruption case. Refined:

- **Normal phase transitions** use Handoff: artifacts + narrative
  summary, evaluator-accepted. Thread-based.
- **Mid-phase resumption** uses Checkpoints: continuous work-state
  snapshots from a separate channel, with compaction.

Both mechanisms preserve the externalization principle. Nothing hidden;
everything visible to other actors who want to look. The difference is
the lifecycle: Handoff is the canonical record of a completed phase;
Checkpoints are operational scaffolding for recovery and discipline
during a phase.

## Deferred for later

- **Compaction agent design.** Whether a dedicated compactor agent
  runs, or compaction is a harness background task. Either works;
  choose when implementing.
- **Deferred-item promotion UX.** The mechanism for promoting a
  deferred item to a new work unit. Probably a thread-or-handoff-level
  decision by the evaluator, with the harness creating the new work
  unit automatically.
- **Backlog concept.** Promoted deferred items become work units in an
  unassigned state. See [99](./99-open-questions.md) — the harness
  needs to represent "exists but not scheduled" work units.
