# 08 — Threads and Communication

The structured communication channel attached to each ticket. The
substrate for problems 1 and 2: reviewer ↔ implementer comms, and
mid-flight human intervention.

## What a thread is

A first-class, visible, gated conversation attached to a ticket. Any
actor — agent or human — can post. Any actor on the team can read. Entries
are typed objects with state and lifecycle, not chat messages.

Threads replace three things that are usually ad hoc or invisible:

- Agent externalized reasoning that other actors need to see (not internal
  chain-of-thought — the externalized kind).
- Review feedback on non-code concerns and its resolution.
- Requests for clarification or help, agent-to-agent and agent-to-human.

Threads **complement** PR comments. Code-level feedback belongs on the PR
where the code lives. Thread entries are for the meta-conversation:
architectural questions, clarifications, decisions, objections on approach.
The workflow declares, per phase, which channel is used (see
[05](./05-workflow-model.md)).

## Entry types

Eleven types, each with distinct gating semantics.

**Question.** Asks for information or judgment. Has a target: specific
actor, specific role, or "any human." Can be marked blocking (asker is
halted until resolved) or non-blocking. Resolves when the asker (not the
answerer) accepts an Answer as sufficient.

**Answer.** Responds to a Question, references it by ID. Does not
auto-resolve the question.

**Objection.** A reviewer or evaluator says "this is wrong." References
the specific artifact (file, line, prior entry, decision). Blocks
ticket completion. Resolves via a Resolution accepted by the objector,
or a Waiver.

**Resolution.** Addresses an Objection with "here's how I fixed it."
Objector (not resolver) accepts it to close the Objection.

**Waiver.** Explicitly overrides an Objection with justification.
Preserves the Objection in the audit trail as waived-with-reason. This is
the policy-as-guide override mechanism from [01](./01-core-concepts.md).
Not hidden; searchable later to audit whether waivers are being abused.

**Decision.** A non-obvious choice made during the work, with rationale.
Inline in the thread for visibility; also extracted into the standalone
decision record artifact. Makes recording decisions cheap at the point of
making them, which is the intervention for problems 4 and 6.

**Handoff.** Marks the end of one phase, ready for the evaluator. Authored
by the completing actor. Contains:

- Output artifact references (what the next phase consumes).
- A short narrative summary (1–3 sentences) — what was done, anything
  unusual, things the next actor should watch for.
- List of items deferred during the phase (from checkpoints).
- Acceptance state (pending / accepted / rejected).

The evaluator accepts (triggering workflow advancement) or rejects
(looping back per the phase's on-failure transition). Evaluator reviews
deferred items and may object to any that should have been done in-phase,
or elect to promote them to new tickets.

Mid-phase interruption is handled by checkpoints, not by partial
handoffs — see [09](./09-checkpoints.md). Handoffs are for completed
phases only.

**Escalation.** "I can't proceed; this is beyond my scope." Targeted by
reason, not by actor — architectural decision needed, policy conflict,
out-of-scope request, resource exhausted. The service routes escalations
to configured handlers per the workflow declaration.

**Uncertain.** "I need input but don't know who should handle this." The
orchestrator resolves by routing — either converting to a targeted
Question or escalating if it can't route. The graceful-degradation path
when an agent isn't sure about role boundaries.

**Note.** Freeform observation that doesn't need resolution. "Tried
approach X, didn't work because Y, switching to Z." Auditable context
without gating anything. Not for status updates (those belong in the
heartbeat/observability layer).

**Proposal.** Request to change a durable owned artifact (spec,
architecture document, coding conventions, etc.). Routed to the owning
role per the project's ownership map. See [04](./04-ownership.md) for
the full ownership model. Carries:

- Target artifact (and section, if section-level ownership).
- Proposed change, specific enough to apply if accepted.
- Rationale.
- Target owner(s).
- State: pending / accepted / rejected / refining.

Resolution asymmetry applies — the proposer cannot self-accept. The
owner accepts (applies the change, creates a new artifact version),
rejects (with reasoning, preserved as a decision record), or requests
refinement (back-and-forth until accepted, rejected, or abandoned).

## Gating semantics

What makes threads more than logs:

**Unresolved blocking entries prevent ticket completion.** Open
objections, unanswered blocking questions, unaddressed escalations. The
workflow can't advance past a gate with unresolved blockers on the current
phase.

**Resolution asymmetry.** The objector resolves the objection, not the
fixer. The asker resolves the question, not the answerer. Prevents the
"I addressed it, marking resolved, moving on" flavor of self-certification
(problem 7 in a different hat).

**Waivers leave an audit trail.** An overridden objection is marked
waived-with-reason, not deleted. Review can later find all waived
objections across the project.

**Some entries auto-resolve.** Notes don't need resolution. Handoffs
resolve when the next phase starts. Decisions are informational.

## Deadlock handling

When a blocking entry is waiting on resolution by a specific actor who
hasn't acted within a configured window, the **orchestrator becomes the
resolver of last resort.** It can nudge the blocking actor, reassign to
another qualified actor per the workflow's escalation_targets, or escalate
to a human.

This makes the orchestrator responsible for keeping work moving and
matches its role as scheduler. The specific timeout values are per-phase
(deferred to workflow model detail).

## Targeting constraints

Escalation and targeted Question targets are constrained by the workflow,
not a free-for-all. The workflow phase declares its legal targets with
reasons:

```yaml
escalation_targets:
  - role: planner
    reason: "architectural decisions beyond phase scope"
  - role: reviewer
    reason: "standards/convention clarifications"
  - target: human
    reason: "policy conflicts, resource exhaustion"
```

The reasons are not decorative — they become part of the agent's spawn
context, so it knows why each target exists and when to use which. The
orchestrator enforces the constraints when an agent posts a targeted
entry.

An agent that doesn't know how to target correctly posts an Uncertain
and the orchestrator routes.

**Human escape hatch.** The special targets `human` and `any_human`
always pass the phase allow-list, even when `questions_to` /
`escalation_targets` is restrictive. This preserves the operator
pause UX: a confused agent can always page a human without the phase
author having to remember to enumerate it every time. The constant
is `_HUMAN_TARGET_ESCAPE_HATCH` in `jig/thread_mcp.py`.

## Visibility and propagation

The service's WebSocket event channel publishes thread updates in real
time. Any client subscribed to a ticket gets entries as they arrive.
A dev watching the TUI sees the implementer's question the moment it's
posted. The reviewer's objection appears in the implementer agent's next
tool-result stream.

This makes "anyone can see status at any time" real, and it makes the
dev's TUI essentially a multi-thread client showing active threads across
tickets they follow.

## Who can post, who can see

Any actor attached to a ticket can post. Any actor on the team can
read. No private channels. If it's about the work, it's visible.

## Agent tools

The harness provides thread tools to agents natively (not via MCP, but
invested in at the level of MCP server documentation — see note below):

- `thread_ask(target, question, blocking=bool)` — post a Question.
- `thread_answer(question_id, text)` — respond to a Question.
- `thread_object(target_artifact, text)` — raise an Objection.
- `thread_resolve(entry_id, text)` — post a Resolution (objector closes it).
- `thread_waive(entry_id, justification)` — post a Waiver.
  Authorization: `sender`'s role must declare
  `capabilities.waivers.can_waive` including `"objection"` (for
  `thread_waive`) or `"check_failure:<severity>"` (for
  `thread_waive_check`). Declared on the role template or broadened
  via phase `capability_overrides`.
- `thread_decide(decision, rationale)` — record a Decision.
- `thread_handoff(outputs, summary)` — mark phase complete with output refs and narrative summary.
- `thread_escalate(reason, details)` — escalate by reason.
- `thread_uncertain(details)` — route-me.
- `thread_note(text)` — freeform observation.
- `thread_propose(target_artifact, change, rationale)` — propose a change
  to a durable owned artifact; routed by the orchestrator per the
  ownership map.

Tool descriptions are treated as part of the agent's usable context: each
tool has structured guidance about when to use it vs. adjacent tools, with
examples. This is the "well-documented native tools substitute for MCP"
point — the investment is in the descriptions, not the transport.

A `harness_capabilities()` meta-tool is worth considering for
introspection — lets an agent reason about its own tool surface the way
it would with MCP.

## Agents should not dump reasoning here

The thread is for things other actors need to see. An agent's internal
working context (intermediate thoughts, failed approaches it's discarding)
doesn't belong in the thread. Discipline enforced through prompting and
tool descriptions; not a hard technical constraint.

## Thread as context for later spawns

Prior thread entries on a ticket are part of the ticket context
bundle. An agent spawned into an in-progress ticket receives existing
thread state at spawn. A second attempt at a failed phase picks up where
the first left off. A reviewer sees what the implementer struggled with.

Long threads have token cost. Compaction (a summary-entry type, produced
by the harness or a dedicated agent when threads grow) is deferred;
noted as a future concern.

## Deliberately not included

- Direct messages between actors. If it's about the work, it's on the
  thread.
- Reactions / acknowledgments / emoji. No gating value, adds noise.
- Nested threading in the UI. Entries reference each other by ID;
  display is flat.
- Edit history on entries. Entries are immutable. Mistakes get a
  follow-up entry.

## Relationship to PR comments

Threads do not replace PR review comments. Code-level feedback belongs on
the PR. Threads carry the meta-conversation. The workflow's review phase
declares `output_channel: thread | pr_comments | both` depending on what's
appropriate at that phase. Agent pre-PR code review might stay in the
thread (fast, clean). Human PR review happens on the PR (where humans
live). See [05 — Workflow model](./05-workflow-model.md).

No cross-linking machinery between the two. If the PR needs information
from the thread, the PR-creation phase is responsible for putting that
information in the PR description.
