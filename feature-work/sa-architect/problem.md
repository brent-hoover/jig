---
title: SA as Architect — Problem Statement
type: problem
status: draft
owner: Brent Hoover
created: 2026-06-07
updated: 2026-06-07
---

# SA as Architect — Problem Statement

## Context

When a jig project initializes, the SA agent runs after the PO conversation and spec generation. It
reads the structured spec, picks a scaffold template, and outputs a `decisions` dict (framework,
HTTP client, async mode, package manager, etc.) along with a `constraints` list that dev agents are
expected to honor. This output is recorded in the architecture ticket thread and passed to
`apply_scaffold`.

The SA makes these decisions by reasoning from its training data. It has no mechanism to look up
current library documentation, verify package versions, or check whether its assumptions about
project dependencies match their actual current behavior.

## Problem

SA decisions are ungrounded. Because SA reasons from training data alone, the same brief can produce
different decisions across runs — and neither run has any basis for preferring its answer over the
other. On two consecutive hn-cli evals against the same brief, SA chose `async_io: true` in one run
and `async_io: false` in the other. Both runs produced rationale. Neither checked anything.

Beyond inconsistency, ungrounded decisions carry silent staleness risk: training data reflects
library behavior at a point in time. When a library's recommended usage, version constraints, or API
shape has changed, SA has no mechanism to detect that. The decisions it records in the architecture
proposal and passes to dev agents may be subtly wrong, and no downstream agent has the context to
catch it.

The constraints SA produces are also unverified against real behavior. In the hn-cli case, SA
correctly identified the HN Firebase API as the data source but did not check what its responses
actually contain. That gap propagated into test fixtures via a separate path (the brief vocabulary),
but SA was in the best position to catch it and didn't — it had no way to try.

## Complexity drivers

- **Scale**: N/A — SA runs once per project init. No scaling dimension.
- **Concurrency**: N/A — SA is a single sequential agent. Single-writer.
- **Failure modes**: Ungrounded decisions propagate silently. A wrong framework choice or stale
  package assumption is baked into the scaffold and passed to every dev agent as a constraint. By
  the time a dev agent discovers the problem mid-ticket, the cost to correct it spans multiple
  tickets. There is no current mechanism to flag SA decisions as unverified.
- **Cross-cutting policies**: Grounding SA's decisions requires network egress from the init-phase
  sandbox. Whether init-phase agents are permitted the same egress path as dev and test agents is
  unconfirmed — init runs in a different phase and the sandbox configuration may differ.

## Constraints

- SA runs during `jig init`, after spec generation and before PM planning. Research must complete
  within SA's single run — it cannot hand off to another agent mid-init.
- The existing `decisions` + `constraints` output shape (`sa_propose_scaffold`) must be preserved.
  This is the handoff contract to `apply_scaffold` and downstream agents.
- Research must be targeted — the application surface the project will use, not full library
  documentation.

## Requirements

- SA decisions must be grounded in a verifiable source, not implicit training-data reasoning.
- SA's decisions must be reproducible: the same spec must produce the same decisions across runs,
  or divergence must be tied to an explicit ambiguity in the spec recorded as an open question.

## Non-goals

- Ongoing architectural revision after init — SA decisions are made once at project start.
- Full library documentation coverage — SA researches the project's specific usage surface, not
  the entire API of every dependency.
- Authenticated API research — endpoints requiring credentials are flagged as open questions,
  not probed.

## Success criteria

- Given the same brief on two consecutive runs, SA produces the same `async_io`, `cli_framework`,
  and `http_client` decisions. Divergence is only acceptable when the spec is genuinely ambiguous
  on that dimension, and the ambiguity is logged as an open question in the proposal.
- SA's recorded decisions reference the source that justified them rather than implicit
  training-data reasoning.
- On a re-run of the hn-cli eval, SA's decisions do not contradict verifiable current behavior of
  the named dependencies (e.g. the HN Firebase API's actual response shape).

## Open questions

- [ ] Does the init-phase sandbox permit network egress? Dev and test agents use WebFetch and
  Context7, but init runs in a distinct phase — egress must be confirmed before the simplest
  solution is viable.
- [ ] How much should SA's decisions constrain dev agents — hard invariants that cause escalation
  if violated, or soft defaults a dev agent can override with rationale?
- [ ] When the structured spec is ambiguous about project shape (e.g. async vs sync not implied),
  should SA ask the operator or infer and document the inference?

## Change log

- 2026-06-07: Initial draft (Brent Hoover)
