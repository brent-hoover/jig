# Agent Harness — Architecture Notes

## What this is for

Make agents real teammates on a team that already has good engineering
discipline — without the discipline eroding as agent involvement scales up.

The premise: a well-run engineering team already has practices that produce
good work. Shared context. Clear roles. Structured review. Honest
definitions of done. Visible progress. Decisions captured with rationale.
These practices evolved because they work.

Current agent tooling treats agents as solo contributors. An agent picks up
a task, works alone, and hands back a result. That model doesn't scale —
not because agents are bad at coding, but because they can't participate in
the practices that make teams good. They have no shared context, no
structured way to communicate mid-work, no role boundaries, no accountable
handoffs. Every agent-involved task either bypasses the team's practices or
forces a human to manually bridge the gap.

The harness makes agents first-class participants in those practices. An
agent that joins a work unit arrives with the context a human teammate
would have. It communicates through the same channels. It hands off through
the same gates. It's subject to the same definition of done. A human
reviewing agent work sees the same artifacts they'd see reviewing a
colleague's work, in the same place, with the same structure.

The ambition is that adding more agents to a team makes the team faster
without making it worse. Practices stay tight. Review stays meaningful.
Context stays coherent. The work product is indistinguishable in quality
from what the team produced before — because it's produced under the same
constraints.

## Design goals (derived from the above)

1. **Genuine collaboration.** Humans and agents as peers on the same work,
   not humans dispatching tasks to agents.
2. **Universal visibility.** Anyone on the team can see project status and
   current agent activity at any time, in real time.
3. **Consistent practice.** Best practices enforced identically for devs
   and agents — one policy, two enforcement points.
4. **Agent-to-agent work.** Planning, review, QA, not just coding.
5. **Verifiability.** The system determines when work is done. Agents
   cannot self-certify past objective gates.

## Terminology

- **Agent** = Claude Code instance.
- **Dev** = human developer.
- **Dev agent** = an agent operating in a dev role. Still an agent.

## Document index

Decision records (closed topics):

- [01 — Core concepts](./01-core-concepts.md)
- [02 — Project spec](./02-project-spec.md)
- [03 — Specs and work types](./03-specs-and-work-types.md)
- [04 — Ownership and owner roles](./04-ownership.md)
- [05 — Workflow model (partial)](./05-workflow-model.md)
- [06 — Agent identity and templates](./06-agent-identity.md)
- [07 — Context bundles](./07-context-bundles.md)
- [08 — Threads and communication](./08-threads.md)
- [09 — Checkpoints](./09-checkpoints.md)
- [10 — Verification](./10-verification.md)
- [11 — State location](./11-state-location.md)
- [12 — Service shape and protocol](./12-service-shape.md)
- [13 — SCM integration](./13-scm-integration.md)
- [14 — Service internals](./14-service-internals.md)

Open questions and deferred decisions:

- [99 — Open questions](./99-open-questions.md)

## Specific failure modes this design addresses

From prior attempts at building similar systems. Each is a concrete way
that "agents as real teammates" breaks down if not designed for. These
are the forcing functions behind most of the design decisions — the
ambition above sets the direction; these constrain the shape.

1. Reviewer ↔ implementer agents have no structured way to communicate, and
   when they do, it's not visible to anyone else.
2. Humans need to intervene in agent-to-agent exchanges but have no clean
   injection point.
3. Agents arrive undereducated about the project — they thrash trying to
   understand conventions, wasting tokens and producing lower-quality work.
4. No consistent memory, either project-wide or per-agent.
5. Hard to scale the process across different-sized units of work.
6. Agents lack "big picture" context that would let them make smarter
   autonomous decisions.
7. Agents declare work done when it isn't.
