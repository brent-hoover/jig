# 13 — SCM Integration

The harness owns coordination. The source control management platform
(GitHub, GitLab, etc.) owns code. Several workflow phases need to cross
that boundary: create a branch, open a PR, read PR state, post PR
comments, detect merges. The SCM integration layer is the plug that
connects the two without the harness trying to replace the SCM.

## Premise

From [08](./08-threads.md) and [05](./05-workflow-model.md):

- The harness carries the collaboration layer (work units, threads,
  phases, ownership).
- The SCM carries the code layer (branches, commits, PRs, code review).
- Both are authoritative in their domains.

The integration layer is narrow and well-defined. The harness doesn't
become a PR-review UI; the SCM doesn't become a work-tracking system.
They integrate at specific seams.

## Scope of the integration

What the harness needs from the SCM:

**Branch creation.** At PR-creation phase, the harness (or the agent
filling that phase) creates a branch from a base branch.

**Commit observation.** The harness observes commits on relevant
branches — commit hashes feed into check runs (so a check run knows
what state it verified), and commit messages feed into the audit trail.

**PR creation.** The PR-creation phase opens a PR with a description
assembled from the work unit (spec summary + any PR-specific content the
creating actor produces). The PR URL is recorded on the work unit.

**PR state reading.** The harness reads PR state: open/closed,
review status, CI status, merged/not, who approved. State changes
drive phase transitions (merge triggers work-unit closure).

**PR comment posting.** When a workflow phase has
`output_channel: pr_comments` (or `both`), comments from that phase
are posted to the PR via the SCM API.

**PR comment reading.** Human PR reviewers leave comments on the SCM.
The harness reads them back — resolved PR review threads count as
resolved evaluator feedback; unresolved blocks phase advancement.

**Merge detection.** When a PR merges, the harness notices. Triggers
the work unit's terminal phase or closure, depending on workflow.

That's roughly the full scope. Not everything the SCM does — just what
the harness needs.

## Pluggable adapter pattern

The harness defines an interface; adapters implement it per platform.
GitHub is the likely first implementation; GitLab second; others as
needed.

The interface is intentionally narrow. Adapters don't expose every
SCM feature — just what the harness needs. This keeps adapters small
and consistent across platforms.

Rough interface shape (not a specific API, just the surface):

- `create_branch(base_ref, new_branch_name)` → commit SHA
- `create_pr(branch, base, title, description)` → PR ID, URL
- `read_pr_state(pr_id)` → {status, reviews, checks, mergeable}
- `post_pr_comment(pr_id, body, target_file?, target_line?)` → comment ID
- `read_pr_comments(pr_id)` → list of comments with state
- `subscribe_pr_events(pr_id)` → stream of state changes
- `detect_merge(pr_id)` → merged? with merge commit if so

Each adapter maps these to its platform's API. GitHub's adapter uses
the REST/GraphQL APIs and webhooks. GitLab's adapter uses its
equivalents.

## Webhooks vs polling

SCM state changes (PR approved, CI finished, merge happened) can be
observed two ways:

**Webhooks.** SCM pushes events to the harness. Low latency. Requires
the service to be reachable from the SCM (real concern in some
deployments; fine for cloud-hosted SCMs reaching out to a cloud-hosted
harness; harder for on-prem harnesses).

**Polling.** The harness periodically asks the SCM for state. Higher
latency, simpler deployment. No inbound reachability requirement.

Adapters should support both. Default to webhooks when available and
reachable; fall back to polling otherwise. The integration layer
abstracts which is in use; downstream code doesn't care.

For reliability: even when webhooks are primary, periodic reconciliation
polling catches missed events. Webhooks are best-effort; reconciliation
is correctness.

## Authentication

Each adapter needs SCM credentials — a token with scopes appropriate to
what the harness does:

- Read repository, read PRs, read PR comments.
- Create branches, create PRs, post PR comments.
- Optionally: perform merges (only if the merge-ownership question
  lands on "harness merges").

Credentials are per-project (service configuration) and service-side
only. Agents never see them. Agent sandboxes can't reach SCM directly;
all SCM interaction goes through the service.

This is part of why the "sandbox → service → SCM" shape matters. It
keeps credentials out of agent context and makes SCM access auditable
at a single point.

## Agent identity in SCM

When the harness creates a branch, opens a PR, or posts a comment, the
SCM sees some author. Who is it?

Options:

- **A single bot identity.** "harness-bot" or per-project equivalent.
  Every harness-initiated action is attributed to this identity. Simple;
  easy to identify harness actions in the SCM; harder to trace back to
  which agent did what without looking at the harness.
- **Per-agent identities.** Each agent has its own SCM identity.
  SCM-native audit trail shows which agent did what. Harder to set up
  (need to mint and manage SCM identities per agent), may run into SCM
  licensing costs (some platforms charge per user).
- **Bot identity with metadata.** Single bot identity; harness posts
  include metadata (as PR description fields, comment tags) identifying
  the specific agent instance. Simpler auth; still traceable.

V1 default: bot identity with metadata. Simpler auth, still traceable,
avoids per-agent licensing issues. Teams that want per-agent identities
can configure it; the adapter supports both.

## PR description generation

The PR-creation phase produces a PR description. What goes in it?

A reasonable default: the work unit spec summary, the list of
capabilities addressed, and links back to the harness work unit for
humans who want deeper context. Nothing about the thread, the deferred
items, the internal verification state — those are harness concerns,
not PR concerns (per the earlier call in the threads discussion).

The create-pr phase's agent generates this based on the work-unit spec
and a project-level PR template. Projects can override the template:

```yaml
pr_template: |
  ## Summary
  {spec.summary}

  ## Changes
  {spec.behaviors | formatted}

  ## Linked work unit
  {work_unit.id}

  ---
  {custom_team_content}
```

The template uses spec fields; no harness-internal state leaks into the
SCM. Reviewers see a PR that looks like any other PR on the project,
with appropriate detail.

## PR comment round-tripping

When a workflow phase declares `output_channel: pr_comments` (see
[05](./05-workflow-model.md)), comments produced by that phase are
posted to the PR. Used for:

- Agent reviewers posting code-level comments.
- QA validation findings (when workflow configures this).
- Automated check results posted as PR comments for visibility.

The reverse direction — humans posting comments on the PR that the
harness needs to see — is required for human-review phases. The
adapter reads PR comments and maps them to thread entries:

- Unresolved PR review threads → open evaluator objections.
- Resolved PR review threads → resolved objections.
- PR approval → evaluator acceptance.
- Change requests → phase rejection, loop back.

The mapping is adapter-specific because SCM models differ (GitHub has
review threads; GitLab has discussions; terminology varies). The
adapter normalizes to the harness's thread/objection model.

## CI integration

A common case: the SCM has CI that runs on PRs (GitHub Actions,
GitLab CI, Jenkins, etc.). Those CI results are relevant to the
harness — they're automated checks from the harness's perspective, just
running elsewhere.

Adapter reads CI status:

- CI pending → automated check pending.
- CI passed → contributes to phase verification.
- CI failed → check-failure entry on thread, phase cannot advance.

This means the check catalog includes both harness-run checks and
SCM-run checks. Both are automated checks; their type differs only in
where they execute. Phases declare which they depend on.

Running CI in the SCM rather than the harness is often better:
- Existing CI infrastructure, pipelines, tooling.
- Parallel execution, caching, artifact handling.
- Team familiarity.

The harness integrates rather than replaces.

## Merge ownership — resolved

The merge-ownership open question gets its answer here: **the harness
observes merges, does not perform them.**

Reasons:

- Keeps the harness out of the SCM's UX.
- Matches how humans actually merge (they want the SCM button they
  know).
- Avoids the harness having merge-authorization logic duplicating the
  SCM's.
- Simplifies credential requirements (no merge scope needed).

The terminal phase of a standard workflow is "awaiting merge" — human
clicks merge on the SCM, adapter detects it via webhook or polling,
work unit closes.

Projects that want the harness to perform merges (e.g., automated
merge after all approvals) can configure this; it's supported but not
the default. The SCM credential scope required is broader; the
team opts in explicitly.

## Branching model

The harness is agnostic to branching model. It creates a branch from
a declared base (usually `main` or `develop`) and opens a PR to that
base. Projects with complex branching (trunk-based, GitFlow, release
branches) configure the base branch per workflow or per work-unit
type.

No built-in concept of "feature branch namespace" or "integration
branches" — those are team conventions the configuration captures.

## Failure modes

SCM integration fails for external reasons regularly: network blips,
rate limits, credential expiry, platform outages. Adapter handles:

**Retry with backoff.** Transient failures retry. Persistent failures
surface as escalations to human.

**State reconciliation.** After service restart or prolonged
disconnect, adapter reconciles SCM state with harness state. Missed
events detected via periodic polling even when webhooks are primary.

**Graceful degradation.** If SCM is entirely unavailable, work units
can still progress through phases that don't require SCM interaction.
SCM-dependent phases wait; humans see the waiting state clearly.

**Credential rotation.** Adapter supports credential rotation without
service restart. Critical for long-running deployments.

## What this doesn't cover

- **Git operations.** The harness uses SCM APIs, not raw git. Agents
  working in sandboxes use git within their sandbox for their own
  commits, but the harness-SCM boundary is API-level. Raw git access
  is not part of the SCM integration.
- **Repository hosting concerns.** Storage, replication, backups are
  the SCM's problem.
- **SCM-native code review features.** Threaded discussions, review
  approval state machines, suggested changes — those live in the SCM.
  The harness reads summary state but doesn't replicate the UX.

## V1 scope

- GitHub adapter as the reference implementation.
- Webhook + polling support with webhook preferred.
- Bot identity with metadata.
- Merge observation only (not performing merges).
- PR comments bidirectional (posting and reading).
- CI status reading as automated check input.

## Deferred

- **GitLab adapter.** Second platform support. Built when needed.
- **Per-agent SCM identities.** Simpler auth model first.
- **Harness-performed merges.** Opt-in configuration; default is
  observe-only.
- **Advanced branching models.** Configurable base branches; more
  sophisticated patterns as needed.
- **SCM-side automation.** Replacing CI pipelines with harness-run
  checks. Out of scope; integrate with existing CI.
