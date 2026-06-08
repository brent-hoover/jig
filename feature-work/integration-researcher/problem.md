---
title: Integration Grounding — Problem Statement
type: problem
status: draft
owner: brent-hoover
created: 2026-06-07
updated: 2026-06-08
---

# Integration Grounding — Problem Statement

## Context

When jig scaffolds a project that uses external integrations (HTTP APIs, SDKs, third-party services),
dev and test agents already have access to grounding tools — Context7 is in their default
`allowed_mcps`; WebSearch and WebFetch are in their default `allowed_tools`. What doesn't exist
is an enforced step that uses those tools before PM planning, or a shared artifact that captures
what was found. Each agent independently decides whether to
look something up, and the results — if any — are not shared with other agents working on the same
project.

The project workflow today is: PO conversation → scaffold → PM planning → agent execution. There is no
step that produces a shared, verified ground-truth for external integrations before agents start writing
code and tests.

## Problem

Brief vocabulary leaks into agent assumptions, producing mocks and implementations that diverge from the
real API. This is not a hypothetical risk — it produced a concrete failure on the `hn-cli-20260524T144910Z`
eval:

- The brief described filtering by `type` with values `"ask"`, `"show"`, `"story"`, `"job"`.
- The test agent wrote fixtures with `"type": "ask"` and `"type": "show"`.
- The real HN Firebase API always returns `"type": "story"` and uses `"Ask HN:"` / `"Show HN:"` title
  prefixes — the `type` field values the brief named do not exist in live responses.
- The dev agent built to those fixtures. The shipped feature returned empty results against the live API.
- Two of the four reviewer-caught bugs traced directly to this docs-vs-reality gap.

The problem has two parts: agents share no integration knowledge (each agent independently decides whether
to look things up, and findings aren't persisted), and there is no enforced verification step that checks
brief claims against what the API actually returns. Telling dev and test to "use Context7" doesn't close
this — both roles can already do that, and they still built to wrong fixture shapes.

## Complexity drivers

- **Scale**: N/A — bounded by the number of integrations per project, which is small (typically 1–3). No
  non-linear growth.
- **Concurrency**: N/A — grounding runs once per scaffold, before PM planning. Single-writer.
- **Failure modes**: If integration grounding produces inaccurate information, downstream agents work from
  a false ground truth — potentially worse than no grounding. Live HTTP fetches can fail, time out, or
  return unhelpful responses (auth walls, rate limits). Grounding must fail gracefully without blocking
  the pipeline. Authenticated APIs carry real credential-handling blast radius if mishandled.
- **Cross-cutting policies**: Authenticated APIs involve secrets. The boundary between unauthenticated
  (safe to probe) and authenticated (operator must provide) must be explicit and enforced, not advisory.

## Constraints

- Grounding must complete before PM planning — it produces information PM planning depends on.
- Authenticated APIs are out of scope. The boundary must be explicit and enforced.
- No new runtime dependencies beyond what jig already includes.
- Integration detection must not require the operator to hand-annotate every brief — it should work
  from existing brief/spec content.

## Requirements

- External integration behavior must be verified against real sources before dev and test agents start
  work.
- Verified integration information must be shared across all agents working on the same project — not
  rediscovered independently per agent.
- Unverified brief claims about API behavior must not reach dev and test agents as the only source of
  truth.
- Authenticated APIs must be explicitly flagged rather than silently skipped or probed.

## Non-goals

- Authenticated API support (credentials, OAuth flows).
- Doc regeneration / version-bump staleness detection — not a problem for one-shot evals.
- Dropping raw sample responses as test fixture files for direct agent consumption — out of scope for now.
- Explaining general-purpose tools (Postgres, Redis, stdlib) beyond the project's specific usage.
- Full integration API coverage — only the endpoints the project actually uses.

## Success criteria

- Before dev and test agents start work, real sample responses for detected external integrations are
  available as a shared project artifact — not reconstructed from brief vocabulary.
- Fixtures for a detected integration contain only values present in those real samples — no fixture
  contains a brief-described value absent from actual API responses (the `type: "ask"` class of bug).
- The hn-cli bug class (brief-described field values that don't exist in live API responses) does not
  recur on a fresh eval of an equivalent brief.

## Open questions

- [ ] How are integrations detected? Spec generator pattern-matching on URLs and "API" keywords in the
  brief may miss integrations buried in capability prose — is PO probing the safer first line?
- [ ] What happens when grounding fails or produces uncertain results — does it hard-block PM planning,
  warn and proceed, or produce a flagged artifact?
- [ ] SDK-only integrations (no curl-able endpoints): no live sample is possible. Acceptable for now?

## Change log

- 2026-06-07: Initial draft (Brent Hoover)
- 2026-06-08: Fix owner field format (brent-hoover)
