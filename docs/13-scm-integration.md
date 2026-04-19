# 13 — SCM Integration

The harness owns coordination. The source control management platform
(GitHub, GitLab, etc.) owns code. Several workflow phases need to cross
that boundary: create a branch, open a PR, read PR state, post PR
comments, detect merges. The SCM integration layer is the plug that
connects the two without the harness trying to replace the SCM.

This doc covers two things: the operational shape (mostly-local vs
hybrid — where the ticket-of-record lives, what jig writes to the SCM),
and the mechanics (adapter interface, webhooks, comment round-tripping,
CI mapping, merge ownership).

## Premise

From [08](./08-threads.md) and [05](./05-workflow-model.md):

- The harness carries the collaboration layer (tickets, threads,
  phases, ownership).
- The SCM carries the code layer (branches, commits, PRs, code review).
- Both are authoritative in their domains.

The integration layer is narrow and well-defined. The harness doesn't
become a PR-review UI; the SCM doesn't become a work-tracking system.
They integrate at specific seams.

## Two modes

Two common arrangements of where the ticket-of-record lives:

- **Mostly-local.** Tickets are a jig concept. The external SCM is
  a code host; no issue tracker involvement at the ticket level.
  Terminal delivery is a push + PR, or a local merge + push-main.
- **Hybrid.** Tickets mirror issues in the external SCM
  (GitHub/GitLab/Gitea). Humans file issues where they already do;
  jig picks them up and runs the lifecycle. Terminal delivery is
  always a PR that closes the issue on merge.

These differ in where the ticket-of-record lives, who creates it, and
how much of the SCM jig is expected to manage. Rather than let each
workflow re-litigate this, the mode is declared once per repo.

## Configuration

Single block in `.jig/config.yaml`:

```yaml
scm:
  adapter: github              # github | gitlab | gitea | ...
  repo: org/my-project
  mode: hybrid                 # local | hybrid
  cardinality: strict          # strict | loose  (hybrid only)
  writeback:
    comments: default          # minimal | default | verbose
    assignee: leave             # leave | bot | <user>
```

- `mode: local` — mostly-local. No issue sync.
- `mode: hybrid` — issue sync enabled. Tickets link to external
  issues.
- `cardinality: strict` (default for hybrid) — every ticket has a
  linked issue; jig-first creation also creates an issue.
- `cardinality: loose` — tickets may or may not link to an issue;
  issue creation is on-demand.

Mode is per-repo. Mixing modes in a single repo is not supported —
mixing produces confusing lifecycle questions (why does this ticket close
the issue and that one doesn't?) that aren't worth the flexibility.
Loose cardinality is the escape hatch for per-ticket opt-out within
hybrid.

## Scope of the integration

What the harness needs from the SCM, regardless of mode:

**Branch creation.** At PR-creation phase, the harness (or the agent
filling that phase) creates a branch from a base branch.

**Commit observation.** The harness observes commits on relevant
branches — commit hashes feed into check runs, commit messages feed
into the audit trail.

**PR creation.** The PR-creation phase opens a PR with a description
assembled from the ticket. The PR URL is recorded on the ticket.

**PR state reading.** Open/closed, review status, CI status,
merged/not, who approved. State changes drive phase transitions.

**PR comment posting.** Phases with `output_channel: pr_comments` post
comments via the SCM API.

**PR comment reading.** Human PR reviewers leave comments on the SCM;
the harness reads them back as thread entries.

**Merge detection.** When a PR merges (or a commit lands on `main` for
local-merge workflows), the harness notices.

Hybrid mode adds:

**Issue creation / import.** Either jig creates the issue or imports
an existing one.
**Issue write-back.** Namespaced labels and lifecycle comments on the
linked issue.
**Issue state observation.** External close triggers abandonment.

That's the full scope. Not everything the SCM does — just what the
harness needs.

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
- `detect_merge(pr_id | commit)` → merged? with merge commit if so
- `create_issue(title, body, labels)` → issue ID, URL  *(hybrid)*
- `read_issue(id)` → state, labels, title, body  *(hybrid)*
- `update_issue_labels(id, add, remove)` → ok  *(hybrid)*
- `post_issue_comment(id, body)` → comment ID  *(hybrid)*
- `subscribe_issue_events(id)` → stream of state changes  *(hybrid)*

Local-mode adapters implement only the non-issue subset. Adapters map
these to their platform's API.

## Mostly-local mode

The SCM is a code host only. Flow:

1. Ticket created in jig (`jig new` or TUI). Size and work_type
   declared per [03](./03-specs-and-work-types.md) §The three
   classification axes.
2. Branch created in the repo off the configured base.
3. Work progresses through phases locally. Commits go to the feature
   branch.
4. Terminal-delivery phase is one of:
   - **Push + PR.** `create-pr` phase opens a PR; human-review phase
     consumes PR comments; merge detection closes the ticket.
   - **Local merge.** Workflow ends with a merge-to-main phase that
     the human completes locally (rebase, squash, merge). Jig
     observes the merge commit landing on `main` via the adapter and
     closes the ticket. No PR involved.

The workflow declaration chooses which terminal shape applies.
Shipped workflows offer both variants (`standard` ends with PR;
`standard-local` ends with local-merge).

No issue writes. Jig doesn't post to any issue tracker because there
isn't one to post to.

## Hybrid mode

External issues are the canonical ticket-of-record. Tickets are
jig's execution view of those issues. Flow:

1. **Issue exists in SCM** — filed by a human or another system.
2. **Ticket picks it up**, one of:
   - **SCM-first import.** A human runs `jig import <issue-url>`, or
     jig's inbox surfaces the issue for human pickup. Size and
     work_type get set at import time (from issue labels if present,
     else prompted).
   - **Jig-first creation** (strict cardinality). `jig new` also
     creates the issue in the SCM. The issue gets populated from
     ticket fields.
3. Ticket runs through phases per its workflow. Jig writes status
   back to the issue at phase transitions.
4. PR creation phase opens a PR linked to the issue. Standard SCM
   linking syntax ("Closes #123") in the PR description.
5. PR comments round-trip to the thread.
6. PR approval → merge → SCM auto-closes the issue → jig detects
   both events and closes the ticket.

The issue and the ticket are the same conceptual thing viewed from two
systems. Jig is the execution domain; the SCM is the
communication/visibility domain. Both are authoritative for the things
they own.

### Cardinality

- **Strict** (default). Every ticket has a linked issue. Jig-first
  creation creates the issue before the ticket is live. SCM-first import
  is the other entry path. No ticket exists without an issue.
- **Loose.** tickets may exist without an issue. A dev starting a local
  refactor doesn't have to file an issue. Issue creation from a
  loose-mode ticket is on-demand (`jig issue publish <ticket-id>`) — useful
  when work started scrappy and needs visibility later.

Loose mode exists for teams that want issue sync when convenient but
don't want it forced on every ticket. Strict mode is the default because
it's the clearer contract.

### Inbox

Jig maintains an inbox view of SCM issues eligible to become tickets:

- Issues with a configurable ready-label (e.g., `ready-for-jig`,
  `triaged`, or project-defined).
- Issues assigned to the configured jig-bot or a team member.
- Issues not yet linked to a ticket.

The TUI surfaces this inbox. Import is one click (or `jig import` with
the issue URL). No automatic ticket creation on issue-filing — picking
what's worth running is a human decision, not a label-filter.

## Write-back to the issue (hybrid)

Jig manages as much of the issue as it reasonably can, so the issue
reflects reality without humans manually keeping it up to date.

**Issue body.** Jig-first creation populates the issue body from the
ticket's spec summary + link back to the jig ticket. After that, the issue
body is not rewritten on every phase — that would trigger notification
spam. The body carries a small `<!-- jig-managed -->` section at the
bottom with the link and a state line; the rest is human-editable
freely.

**Labels.** Jig maintains a namespaced label set on the issue:

- `jig/phase:<current-phase>` — updated on phase transitions.
- `jig/status:<state>` — one of `active`, `blocked`, `waiting`, `done`,
  `abandoned`.
- `jig/size:<size>` — set at ticket creation, immutable.
- `jig/type:<work_type>` — set at ticket creation, immutable.

Label prefixes (`jig/`) are namespaced to prevent collision with
human-applied labels.

**Comments.** Jig posts status comments on major lifecycle events:

- ticket opens (issue picked up): "Ticket TKT-042 opened. Running
  workflow `feature-standard`."
- Phase transitions for phases with evaluator acceptance: "Spec phase
  accepted by @alice."
- Blockers surface: "Blocked on unresolved Question; awaiting PO."
- PR opens: handled by the PR-creation phase itself (the PR is the
  comment, effectively).
- ticket closes (success or abandoned): summary comment.

Comment volume is a real concern — too noisy and humans mute the
issue. Default is conservative; `scm.writeback.comments` selects
`minimal`, `default`, or `verbose`.

**State.** Issue close is driven by PR merge, not jig — the SCM's
standard "PR closes issue" semantics do the work. Jig doesn't manually
close issues on successful ticket closure; the PR-merge auto-close is the
canonical path. For abandoned tickets (which don't merge), jig does close
the issue with an abandonment comment.

**Assignments.** On ticket open, jig can assign the issue to the bot
identity or a configured team member (per `scm.writeback.assignee`).
Default: leave assignments alone.

### What jig doesn't write back

- **Issue title.** Humans worded it; jig doesn't rewrite.
- **Milestones / projects / boards.** Team's triage concern.
- **Other labels.** Anything not in the `jig/` namespace.
- **Other comments.** Jig doesn't paraphrase thread entries as issue
  comments. The thread is jig's communication channel; the issue has
  its own. Cross-posting would duplicate noise.

The division is: jig owns a namespaced subset (state-tracking labels,
structured status comments); humans own everything else.

## External close handling (hybrid)

A human closes the issue manually on the SCM while the ticket is
in-flight. Jig treats this as **warn and force-abandon**, with
dependency callouts.

Flow when jig detects an external close (via webhook or reconciliation
poll):

1. ticket transitions to `abandoned` with reason `external_close`.
2. Jig posts a warning comment on the (now closed) issue:
   ```
   Ticket TKT-042 force-abandoned due to external issue close.

   Dependencies affected:
     - TKT-045 (parent; blocked pending resolution)
     - TKT-048 (depends_on TKT-042; blocked)

   To resume, reopen the issue and run: jig resume TKT-042
   ```
3. Dependent tickets (children, `depends_on` references) transition to
   `blocked` with the abandoned ticket cited as cause.
4. In-flight agent sandboxes for the ticket are terminated per standard
   abandonment.
5. The archive is written per [17](./17-directory-layout.md).

`jig resume` is the escape hatch if the close was accidental: reopen
the issue, reopen the ticket from its archive, dependents unblock
automatically.

The warning-and-abandon default errs on the side of making the
mismatch visible. Silently ignoring the close would let the SCM and
jig disagree on reality, which is exactly the failure mode mode
declaration is supposed to prevent.

## Webhooks vs polling

SCM state changes (PR approved, CI finished, merge happened, issue
closed) can be observed two ways:

**Webhooks.** SCM pushes events to the harness. Low latency. Requires
the service to be reachable from the SCM (real concern in some
deployments; fine for cloud-hosted SCMs reaching out to a cloud-hosted
harness; harder for on-prem harnesses).

**Polling.** The harness periodically asks the SCM for state. Higher
latency, simpler deployment. No inbound reachability requirement.

Adapters should support both. Default to webhooks when available and
reachable; fall back to polling otherwise. The integration layer
abstracts which is in use; downstream code doesn't care.

For reliability: even when webhooks are primary, periodic
reconciliation polling catches missed events. Webhooks are
best-effort; reconciliation is correctness.

## Authentication

Each adapter needs SCM credentials — a token with scopes appropriate
to what the harness does:

- Read repository, read PRs, read PR comments.
- Create branches, create PRs, post PR comments.
- In hybrid mode: read/write issues, apply labels.
- Optionally: perform merges (only if the merge-ownership config
  is "harness merges").

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
  Every harness-initiated action is attributed to this identity.
  Simple; easy to identify harness actions; harder to trace back to
  which agent did what without looking at the harness.
- **Per-agent identities.** Each agent has its own SCM identity.
  SCM-native audit trail shows which agent did what. Harder to set up;
  may run into SCM licensing costs.
- **Bot identity with metadata.** Single bot identity; harness posts
  include metadata (as PR description fields, comment tags) identifying
  the specific agent instance. Simpler auth; still traceable.

V1 default: bot identity with metadata. Simpler auth, still traceable,
avoids per-agent licensing. Teams that want per-agent identities can
configure it.

## PR description generation

The PR-creation phase produces a PR description. A reasonable default:
the ticket spec summary, the list of capabilities addressed, links
back to the harness ticket, and (in hybrid mode) the "Closes #N"
line for the linked issue.

Nothing about the thread, the deferred items, the internal
verification state — those are harness concerns, not PR concerns.

The create-pr phase's agent generates this based on the ticket
spec and a project-level PR template. Projects can override the
template:

```yaml
pr_template: |
  ## Summary
  {spec.summary}

  ## Changes
  {spec.behaviors | formatted}

  ## Linked ticket
  {ticket.id}

  {if issue}Closes #{issue.number}{endif}

  ---
  {custom_team_content}
```

The template uses spec fields; no harness-internal state leaks into
the SCM.

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

## Comments: issue vs PR vs thread

Three possible comment channels in hybrid mode:

- **Thread** ([08](./08-threads.md)) — jig-native, the primary
  communication channel for ticket participants.
- **Issue comments** — on the external issue.
- **PR comments** — on the PR (once it exists).

Mapping:

- Issue comments by humans **before PR exists**: not pulled into the
  thread in v0.2. They're visible on the issue; jig doesn't mirror
  them. If a human wants jig to act on something, they use the jig
  TUI/CLI. Rationale: issue-comment sync is a subtle problem
  (threading, ordering, which comments matter) and warrants its own
  design pass.
- **PR comments**: round-trip per the previous section. This is the
  established channel for review feedback.
- **Jig-posted issue comments**: see §Write-back.

## CI integration

A common case: the SCM has CI that runs on PRs (GitHub Actions,
GitLab CI, Jenkins, etc.). Those CI results are relevant to the
harness — they're automated checks from the harness's perspective,
just running elsewhere.

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

## Merge ownership

**The harness observes merges, does not perform them.**

Reasons:

- Keeps the harness out of the SCM's UX.
- Matches how humans actually merge (they want the SCM button they
  know).
- Avoids the harness having merge-authorization logic duplicating the
  SCM's.
- Simplifies credential requirements (no merge scope needed).

The terminal phase of a standard workflow is "awaiting merge" — human
clicks merge on the SCM, adapter detects it via webhook or polling,
ticket closes.

Projects that want the harness to perform merges (e.g., automated
merge after all approvals) can configure this; it's supported but not
the default. The SCM credential scope required is broader; the team
opts in explicitly.

## Branching model

The harness is agnostic to branching model. It creates a branch from a
declared base (usually `main` or `develop`) and opens a PR to that
base. Projects with complex branching (trunk-based, GitFlow, release
branches) configure the base branch per workflow or per ticket
type.

No built-in concept of "feature branch namespace" or "integration
branches" — those are team conventions the configuration captures.

## Identity and linking

Each hybrid-mode ticket carries:

- `issue_url` — full URL to the external issue.
- `issue_id` — adapter-specific ID (GitHub number, GitLab IID).
- `pr_url` / `pr_id` — set once the PR-creation phase runs.

The issue carries (via jig's write-back):

- `jig/` labels as described above.
- Comment on open with the ticket ID and a link back to the jig TUI
  entry.

Lookups work in both directions: "what ticket is this issue?" and "what
issue is this ticket?"

## Creation-time interaction with classification

[03](./03-specs-and-work-types.md) §Creation flow takes `(work_type,
size)` as input. In hybrid mode with strict cardinality, creation
also:

- In SCM-first import: reads `jig/type:*` and `jig/size:*` labels
  from the issue if present; prompts the importer if not.
- In jig-first creation: pushes the chosen `(work_type, size)` to the
  issue as labels during issue creation.

Nothing about classification resolution changes; the axes are still
the same. Hybrid mode just adds a bidirectional label sync around the
creation event.

## Failure modes

SCM integration fails for external reasons regularly: network blips,
rate limits, credential expiry, platform outages. Adapter handles:

**Retry with backoff.** Transient failures retry. Persistent failures
surface as escalations to human.

**State reconciliation.** After service restart or prolonged
disconnect, adapter reconciles SCM state with harness state. Missed
events detected via periodic polling even when webhooks are primary.

**Graceful degradation.** If SCM is entirely unavailable, tickets
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
- Both modes supported (`local`, `hybrid`).
- Strict cardinality as the hybrid default.
- Webhook + polling support with webhook preferred.
- Bot identity with metadata.
- Merge observation only (not performing merges).
- PR comments bidirectional (posting and reading).
- Issue write-back (namespaced labels, lifecycle comments).
- CI status reading as automated check input.

## What this does for the original problems

- **Problem 1** (agent ↔ reviewer have no structured visible
  communication): hybrid mode extends visibility to team members who
  already live in the SCM. Issue gets status updates; PR gets review
  comments; threads remain the jig-side source of truth.
- **Problem 2** (humans need clean injection points): issue comments
  and PR comments are existing injection points humans already know.
  Mode declaration means those points have well-defined semantics
  rather than ad-hoc handling per team.

## Deferred

- **Mode mixing per ticket.** Some repos may want hybrid for user-facing
  work, local for internal refactors. Deferred — mode-per-repo is the
  default, `cardinality: loose` is the escape hatch for opt-out tickets.
- **Issue-comment mirroring into thread.** Routing rules for "which
  issue comments become thread entries" is its own design pass.
- **Issue templates matching ticket schemas.** Auto-generating a GitHub
  issue template per work_type so human issue authors fill in the
  structured fields jig needs. Plausible but warrants separate
  design.
- **Multi-issue tickets.** A single ticket referencing multiple issues (epic
  tracking). Deferred — XL tickets are parents per [03], and each child
  can have its own issue in hybrid-strict; the parent tracks the
  collection.
- **Cross-repo issue linking.** Issues in a tracker-only repo
  coordinating work in a code repo. Out of scope for v0.2.
- **Bot → human handoff on abandonment.** When a ticket abandons due to
  external close, auto-notify the relevant human (assignee, reporter)
  beyond the issue comment. Plausible enhancement.
- **PR-less local merge observation via git.** Mostly-local mode's
  local-merge variant relies on the adapter detecting the merge
  commit. Adapter details for this are straightforward but spelling
  out the webhook/poll flow for local-merge is deferred until the
  first adapter implementation.
- **GitLab adapter.** Second platform support. Built when needed.
- **Per-agent SCM identities.** Simpler auth model first.
- **Harness-performed merges.** Opt-in configuration; default is
  observe-only.
- **Advanced branching models.** Configurable base branches; more
  sophisticated patterns as needed.
- **SCM-side automation.** Replacing CI pipelines with harness-run
  checks. Out of scope; integrate with existing CI.
