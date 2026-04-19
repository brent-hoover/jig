# 10 — Verification

The mechanism between "agent claims ready" and "system marks done." The
direct answer to problem 7. Work units don't advance because someone says
they should — they advance because evidence shows the definition-of-done
is satisfied.

## Premise

"Done" is a state the system assigns, not one the implementing actor
claims. Every phase transition requires evidence. Evidence comes from
two categories:

- **Automated checks** — mechanical, executable, deterministic verdicts.
- **Evaluator judgment** — a different actor (human, agent, or both)
  reviewing against a rubric.

Neither alone is sufficient for most phases. A passing test suite with a
rejected review doesn't advance. An accepted review without passing
tests doesn't advance. The two compose.

Agents can post Handoff entries claiming "ready for review." They
cannot mark themselves done. The system's verification process is the
gate.

## Where verification fits in the phase lifecycle

Per phase:

1. Completing actor posts a Handoff (artifacts, summary, deferred items).
2. Harness runs automated checks against current state.
3. Evaluator(s) review the handoff, artifacts, check results.
4. Evaluator accepts → workflow advances. Rejects → loops back per
   on_failure.

The evaluator has check results *before* making their judgment. They
don't wonder if tests pass; the harness already knows. Evaluator
judgment is specifically on what checks can't answer.

## Check taxonomy

Three check flavors, all first-class in the check catalog:

**Scripted checks.** Deterministic, mechanical. Runs a command, reads
exit code and output. Tests, linters, type-checkers, security scanners,
coverage tools, custom project scripts. The baseline verification layer.

**Implementation-aware agent checks.** Agent judgment with full code
visibility. Security review, architectural review, API surface
consistency. An agent spawned with read access to the implementation,
asked a specific question, producing a verdict.

**Black-box agent checks.** Agent judgment with information asymmetry
enforced. Cannot see the implementation; sees only the external
surface (UI, API, CLI) and the spec's behavioral sections. Derives
tests from spec; verifies behavior; reports symptoms without
implementation cause analysis. This is where validation-as-QA happens.

The asymmetry in the third category is enforced by the `excluded`
primitive in context bundles (see [07](./07-context-bundles.md)). The
check-agent literally cannot read excluded paths — context bundle
resolution and tool-level constraints both enforce.

## Check declaration

Checks live in a project-level catalog. Each declares what it is and
how to run it:

```yaml
checks:
  run-tests:
    type: scripted
    command: pytest
    severity: required
    working_dir: .
    timeout_s: 600

  type-check:
    type: scripted
    command: mypy .
    severity: required
    timeout_s: 120

  security-review:
    type: implementation_aware_agent
    template: security-reviewer
    context:
      - repo://src/**
      - project://security-policy
    severity: required
    timeout_s: 300
    max_tokens: 50000

  qa-validation:
    type: black_box_agent
    template: qa-validator
    context:
      - workunit://spec.behaviors
      - workunit://spec.acceptance_criteria
      - workunit://spec.edge_cases
      - workunit://pr.preview_url
    excluded:
      - repo://src/**
      - workunit://spec.design
      - workunit://thread
    severity: required
    timeout_s: 900
    max_tokens: 80000
```

Workflow phases select which checks apply to their completion:

```yaml
- name: implement
  automated_checks: [run-tests, type-check, lint]

- name: pre-pr-security
  automated_checks: [security-review]

- name: validate
  automated_checks: [qa-validation]
```

## Check severity

Three levels:

- **required** — must pass for phase to advance.
- **warning** — failure produces a thread Note; doesn't block.
- **info** — result recorded; never blocks; usually for tracking.

Warning and info levels matter especially for agent checks, which are
more likely to produce noisy results than scripted checks. A team
calibrating a new agent check runs it as warning until confidence
builds, then promotes to required.

## Waivers on required checks

Required checks can be waived in individual cases, with justification:

- Waiver is an explicit action by an authorized actor (human, typically
  owner-role or evaluator).
- Waiver is thread-visible, preserved in audit trail.
- Phase can advance with waived check.
- Waived check failures searchable later — if waivers become common,
  that's audit-visible as a pattern.

This parallels the Waiver mechanism on Objections in [08](./08-threads.md).
Same pattern: strong default, explicit override, recorded justification,
audit-visible.

Who can waive: declared per project. Defaults: PO can waive product-
behavior checks (asymmetric validation failures); SA can waive
technical checks (type errors, security findings with context); any
dev can waive warnings (doesn't apply to required).

## Check failure vs evaluator rejection

Different thread entries, different resolution paths:

**Check failure.** Scripted check failed or agent check returned
negative verdict. Harness produces a check-failure entry referencing
the handoff. The implementing agent can address it — fix the test, fix
the lint error, address the QA finding — and resubmit the handoff
without evaluator involvement. Tight loop the harness manages.

**Evaluator rejection.** Evaluator reviewed the handoff and says no.
Produces an Objection on the thread. Resolution asymmetry applies
(objector resolves). Implementing agent addresses objection, posts
Resolution, objector accepts. Existing thread mechanism.

The distinction matters. Check failures are objective. Evaluator
rejections are subjective and need the thread's resolution machinery.

## Evaluators

Evaluator assignment is a **phase property**, not a role property. The
same role (e.g., reviewer) might be an evaluator in one workflow and
not another. This was an open question; resolving it here: **phase
declares its evaluator(s).**

Evaluator assignment types:

- `previous_phase_role` — the actor who filled a specified role in a
  prior phase evaluates this one. Useful for "the reviewer from the
  code-review phase evaluates the subsequent fix."
- `specific_role` — any actor in the named role evaluates. Harness
  spawns an agent or waits for a human claim per the phase's assignment
  mechanism.
- `automated_only` — phase acceptance is fully determined by automated
  checks; no human judgment needed. Suitable for phases like
  "create-pr" or "run-integration-tests."
- `specific_human` — named human must evaluate. Used for approval gates.
- `multi` — multiple evaluators required. "Both agent-reviewer and
  human-reviewer accept." Either can reject.

**A phase's evaluator cannot be the same actor as the completing actor.**
Hard rule. The harness enforces identity comparison at phase
transition. If resolution would put the same actor in both roles, the
harness escalates — reassigns the evaluator or halts for intervention.
This is how "can't self-certify" becomes structural rather than
aspirational.

For one-person teams, this means work progresses through stages but the
single human switches contexts deliberately. When they wear the
completer hat, they commit; when they wear the evaluator hat, they
come back with fresh eyes. Not as strong as different people, but
better than silent self-approval.

## Evaluator has pre-computed check results

When the harness presents a handoff to an evaluator, it presents:

- The handoff entry (artifacts, summary, deferred items).
- Check results — pass/fail for each check that ran, with output.
- Any check-failure entries on the thread (if the harness ran checks
  and they failed before getting to the evaluator).
- Any waivers applied.

The evaluator's judgment is on what the checks can't answer: does the
handoff narrative make sense, are the deferred items reasonable, is
the shape of the work right, does it feel complete.

If any required check failed, the phase shouldn't have reached
evaluator review — the harness loops back to the completing actor
first. Evaluators shouldn't spend their time telling agents their
tests are failing; that's the check layer's job.

## Re-run policy on rejected handoffs

When an evaluator rejects and the agent addresses the objection with a
new handoff, the harness re-runs all automated checks. Simplest
policy, and usually correct — code changed, check results are stale.

Optimizations (rerun only what could be affected) are possible but
premature. Full re-run is defensible; if it becomes a bottleneck,
optimize later.

## Definition-of-done at work unit closure

A work unit is done when:

1. It has reached a terminal phase in its workflow.
2. The terminal phase is verified (checks pass + evaluator accepted).
3. No unresolved blocking thread entries remain.
4. All deferred items have a disposition: done, explicitly accepted as
   deferred, or promoted to new work units.
5. For parent work units: all required children are done.

The last two prevent "I deferred that" from being a way to evaporate
real work. Deferred items at closure force a deliberate choice: do it
now, acknowledge it's permanently deferred (with reasoning), or
externalize it as a new work unit.

## Check execution environment

Checks run in an environment equivalent to the agent's sandbox. Tests
that passed in the agent's environment should pass when the harness
re-runs them for verification. Environment parity matters — "it
worked on my machine" applies to agents too.

Concretely: the harness runs checks in containers matching the sandbox
image, with the same tool versions, same dependencies, same network
allowlist. Exceptions (e.g., a check that needs SCM write access to
post a PR comment) are declared per-check.

## Agent check quality — the honest trap

The risk: team declares agent checks, sees them passing, feels
covered, stops writing rigorous scripted tests. The agent check drifts
or becomes flaky. The safety net is thinner than it appears.

Guard: agent checks are **additive**, not **replacement**. The check
catalog declares both; workflows use both; teams don't swap scripted
tests for agent checks. Scripted layer handles "each piece works as
specified"; agent layer handles "the whole thing works as experienced."

A phase that has *only* agent checks and no scripted checks should
trigger a warning at project-config validation time: "this phase has
no scripted verification; is that intended?" Teams can proceed but the
harness makes the gap visible.

## Nondeterminism of agent checks

Agent check verdicts can vary across runs on the same state. Policy:

- **Trust the latest.** Most recent run is authoritative. Simplest,
  most conservative, some noise.
- **Re-run on failure.** When an agent check fails, run it once more
  automatically. Require consistent failure to count as failed.
  Reduces flakiness, doubles cost of real failures.
- **Human escalation threshold.** After N nondeterministic disagreements
  (one run passes, next fails, on unchanged state), escalate to human
  to decide if the check is flaky or the system is subtly broken.

Start with trust-the-latest. Add re-run-on-failure if flakiness becomes
real pain. Escalation threshold is a late-stage refinement.

## Audit trail

Verification produces audit records per phase completion:

- Handoff entry.
- Each check run: name, type, verdict, output, timestamp, commit hash.
- Any waivers: who, when, justification.
- Evaluator(s): identity, decision, reasoning.
- Time between handoff submission and verification completion.

Archived with the work unit at closure. Enables questions like "was
test coverage passing when this merged?" and "who accepted this phase
that's now causing problems?"

## Size-as-signal for checks

Not every work unit needs the full check suite. A tiny hotfix
(`size: xs`) shouldn't wait for full security review. Size-appropriate
check selection:

- Workflows declare checks per phase.
- Different workflows (per size) declare different check sets.
- XS workflow's implement phase might run just tests + lint.
- M workflow's implement phase runs tests + lint + type-check.
- L/XL workflow's parent integration phase runs full suite including
  security review and QA validation.

Size-scaling cheapens small work without compromising verification on
large work. Teams calibrate per project.

## Agent check prompting is load-bearing

A scripted check is deterministic; its quality is in the script. An
agent check's quality is in its prompt and context. Poor prompting
produces useless agent checks — either too permissive (passes
everything) or too strict (objects to everything).

Investment in agent check prompts matters the same way investment in
test code matters. Each agent check's template is owned, versioned,
and reviewed the same as any other code artifact. Changes to a
check's prompt are tracked; regression in check quality is detectable.

The harness should support agent check evaluation: a test set of known
good and known bad cases the check agent is run against to calibrate
its behavior. Not in v1, but worth naming — agent checks should be
testable themselves.

## What this does for problem 7

Agents cannot declare work done. They claim ready-for-review via
Handoff. Verification is the system-driven gate between that claim and
actual completion. Every completion has evidence — automated or human
or agent, typically multiple. Every failure produces a visible entry
requiring action. Every waiver leaves a justification record. The
discipline is structural.

## Deliberately deferred

- **Agent check evaluation harness.** Test sets for calibrating agent
  check templates. Important but not v1.
- **Cross-work-unit check sharing.** A check catalog shared across
  projects in a team. Out of scope — one service per repo.
- **Dynamic check selection.** Deciding at runtime which checks to
  run based on what changed. Smart but premature — full re-run is
  defensible and simpler.
- **Check result caching.** Re-running checks that haven't changed is
  wasteful at scale, but premature at v1 scale.
- **Partial check success.** "Check passed for files A and B, failed
  for C." Most check tools don't support this granularity. Treat as
  binary for v1.
