# 99 — Open Questions

Things flagged but not fully resolved, grouped by when they'll need answers.

## Deferred past v1 (named, captured, not blocking)

**Advanced decomposition features.** Basic parent/child decomposition is
v1 (see [05](./05-workflow-model.md), [03](./03-specs-and-work-types.md)).
Deferred: complex dependency graphs among children, partial parent
completion before all children close, mid-flight rebalancing of work
between children, recursion deeper than 2 levels.

**Custom URI resolvers.** The URI scheme is extensible — teams can add
resolvers for `jira://`, `wiki://`, etc. The extensibility point is in
the design; implementation ships with built-in schemes only and adds
custom resolver support when needed.

**Decision record schema.** The structure of decision records beyond
"owner accepted/rejected a proposal with reasoning." Deferred until we
see what owners actually produce in practice. See
[04](./04-ownership.md).

**Thread compaction.** Long threads have token cost. Documented as
deferred in [07](./07-context-bundles.md). Add a summary-entry type when
costs become painful.

**Harness capabilities introspection for agents.** A
`harness_capabilities()` meta-tool that lets an agent reason about its
own tool surface. Partially addressed in [14](./14-service-internals.md)
via the capability introspection endpoint. Could be enriched further.

**Agent check evaluation harness.** Test sets for calibrating agent
check templates. Important, not v1. See [10](./10-verification.md).

**Preemptive scheduling.** Running phases don't get preempted by
higher-priority arrivals. V1 default. See
[14](./14-service-internals.md).

**Multi-tenant service.** One service per repo is the v1 decision.
Monorepos with multiple products, or orgs wanting cross-project views,
would want broader scope. Not v1.

**Per-agent SCM identities.** Bot identity with metadata is the v1
default. Per-agent identities as an opt-in configuration is supported
but not the default. See [13](./13-scm-integration.md).

**Harness-performed merges.** Observe-only is the v1 default. Opt-in
configuration for harness-performed merges is supported. See
[13](./13-scm-integration.md).

## Needs attention during implementation

**Size-as-signal runtime metrics.** What specific metrics indicate that
a ticket is larger than its declared size? Proposal rate, deferral
rate, checkpoint churn are candidates. Shipped defaults to be calibrated
over time per project. See [03](./03-specs-and-work-types.md).

**Threshold values generally.** Deadlock timeouts, check severity
defaults, scheduling priority weights, size-signal thresholds.
Reasonable starting values documented per record; per-project
calibration mechanisms exist; actual tuning happens in use.

**Helper agent pattern generalization.** Noted in [04](./04-ownership.md)
that human-with-helper might be useful beyond just PO/SA. A human
reviewer with an agent helper pre-reading a diff, for example. Not
blocked; the pattern exists, just hasn't been generalized.

**Agent check prompting quality.** Discussed in [10](./10-verification.md).
Load-bearing, team-specific, evolves with use. Documented as
important-in-implementation rather than a design decision.

## Minor or deployment-time

**Ingress configuration for team deployments.** Reverse proxy config
guidance. Documentation, not design. See [14](./14-service-internals.md).

**Service deployment specifics.** Binary + container + systemd. Build
and packaging concerns, not architectural.

**Backlog prioritization beyond a list.** V1 is a simple list view; if
teams want priority sorting, filters, assignment hints, those come
later. See [14](./14-service-internals.md).

**Observability richness.** Metrics, tracing, dashboards. Basic
Prometheus metrics in v1. Richer observability as operational
experience accumulates.

## Worth revisiting after v1

**Cross-ticket coordination.** Ticket A blocked on a decision
that affects ticket B. Currently handled by human decomposition.
May want explicit inter-ticket dependencies later.

**Cross-project capability sharing.** A capability used across multiple
projects. Out of scope now (one service per repo), but teams with
multiple related projects will eventually want this.

**Historical capability queries.** "When did this capability become
built?" is answerable from git history. Dedicated query tooling deferred
until clear need.

**Roadmap visualization.** The project spec is the roadmap. Beyond
reading the Markdown or YAML, no dedicated UI. Worth revisiting if
teams find themselves rebuilding roadmap views elsewhere.

**Spec-driven ticket auto-creation.** When a capability moves to
"ready," offer to create a ticket. Useful but optional; teams do
this manually in v1. See [02](./02-project-spec.md).
