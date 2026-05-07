---
title: Synthetic Operator Simulator — Design
type: design
status: draft
owner: brent
created: 2026-05-03
problem: ./problem.md
---

# Synthetic Operator Simulator — Design

## Summary

A `synthetic-operator` agent role + scenario format + driver that runs scripted v2-workflow lifecycles against the real
daemon end-to-end. Each scenario describes a project shape, a persona, a sequence of operator turns (scripted or
policy-driven), and assertions about expected outcomes. The driver spawns a fresh isolated daemon per run, plays the
scenario, captures analytics events tagged with `simulator: true`, and produces a structured pass/fail report. Coverage
metrics aggregate across runs to identify which workflow paths get exercised and which never trip.

Built parallel with the v2 implementation so workflow design choices get A/B tested as they're made.

## Components

```
docs/v2.0/synthetic-operator/scenarios/             ← scenario library (in-repo)
  smoke/
    po-l1-happy-path.scenario.yaml
    sa-cascade-confirmed-impossible.scenario.yaml
    pm-bones-first-strict.scenario.yaml
    ...
  full/
  nightly/

jig/synthetic_operator/                        ← driver + persona library + assertion framework
  __init__.py
  driver.py                                    ← spawns isolated daemon, plays scenario, captures outcome
  scenario.py                                  ← Pydantic schema for scenarios
  personas/
    methodical.yaml
    fast_and_shippy.yaml
    scope_creeper.yaml
    ambivalent.yaml
    hostile.yaml
  agent.py                                     ← the synthetic-operator agent itself (LLM with persona prompt)
  assertions.py                                ← assertion framework (workflow-gate-reached, artifact-shape, etc.)
  coverage.py                                  ← aggregate analytics events into coverage maps
  realism.py                                   ← realism-budget tracking
  cli.py                                       ← `jig sim run <scenario>` command

.jig/sim/                                      ← per-run output (gitignored)
  runs/<scenario-id>-<timestamp>/
    events.jsonl                               ← captured analytics events from this run
    transcript.jsonl                           ← full operator-turn / system-response trace
    report.yaml                                ← assertion results, coverage delta, pass/fail
    artifacts/                                 ← copy of .jig/ state at end of run for inspection
  realism/
    gaps.jsonl                                 ← logged real-operator-surprises pending simulator extension
    coverage.yaml                              ← aggregate coverage across all runs
```

## Scenario format

YAML script. Self-describing; reviewable; diffable in PRs.

```yaml
# docs/v2.0/synthetic-operator/scenarios/smoke/po-l1-happy-path.scenario.yaml
spec_version: 1
id: po-l1-happy-path
description: |
  L1 discovery walks one persona through one journey to completion.
  Validates the basic 5-phase conversation pattern and Phase-5 playback commit.
tier: smoke                              # smoke | full | nightly
persona: methodical
estimated_cost_usd_max: 0.50

project:
  name: jig-search
  pitch: hosted search SaaS for ecommerce
  problem: |
    Ecommerce sites need fast, faceted search; building it in-house is hard.
  audience: Shopify-store owners with 5-50K SKU catalogs

# Sequence of operator turns. Each turn is what the simulated operator
# would say in response to whatever jig prompted. `assertions` after a
# turn check expected state.
script:
  - turn:
      input: "/init"
    assertions:
      - kind: agent_spawned
        role: l0-po
        within_seconds: 5

  - turn:
      # Synthetic operator's response to L0 PO's pitch question.
      # Either literal text or a policy reference; this is literal.
      input: "An async standup tool for engineering managers."
    assertions:
      - kind: artifact_exists
        path: docs/brief.md
        contains: "async standup"

  - turn:
      input: "/init --proceed"                # operator confirms L0, advances to L1
    assertions:
      - kind: agent_spawned
        role: l1-po
        within_seconds: 5

  # L1 walks through Phase 1 (Frame & primary persona)
  - turn:
      input: "Engineering manager of a 6-person remote team."
  - turn:
      input: "Yes, the team members posting matter too. Manager is primary."

  # Phase 2: elicit primary job
  - turn:
      input: "Spot blockers before her 1:1s, without a 30-min standup call."
  - turn:
      input: "That's the load-bearing one."

  # Phase 3: walk journey
  - turn:
      input: "She gets a Slack ping at 9am summarizing overnight posts."
  - turn:
      input: "She clicks through if something looks off."
  # ... abbreviated for example

  # Phase 5: playback acceptance
  - turn:
      input: "Yes, that's right. Move on."
    assertions:
      - kind: artifact_contains
        path: .jig/spec/discovery.md
        anchor: "{#j-merchant-tuesday-morning}"
      - kind: analytics_event_emitted
        event_kind: ticket_state_changed   # placeholder; real event from L1 finalize
      - kind: artifact_exists
        path: .jig/spec/discovery/playbacks/j-merchant-tuesday-morning.md

  - turn:
      input: "/init --done"                  # operator declares L1 done for now
    assertions:
      - kind: workflow_gate_reached
        gate: po_l1_done
      - kind: agent_completed
        role: l1-po
        status: success

# Final post-run assertions across the whole run
final_assertions:
  - kind: no_orphan_dev_resources
  - kind: no_unhandled_errors
  - kind: cost_under
    usd: 1.00
  - kind: artifact_count_matches
    pattern: .jig/spec/discovery/playbacks/*.md
    count: 1

coverage_tags:
  - po-l0-flow
  - po-l1-five-phase-conversation
  - phase-5-playback-commit
  - operator-confirms-l1-done

# Operator-readable notes for context
notes: |
  This is the canonical "PO L1 happy path" — a methodical operator
  walks through one persona, one journey, accepts the playback, exits.
  If this fails, something fundamental is broken in the L1 PO loop.
```

### Scripted vs policy-driven turns

Two interchangeable forms for `turn.input`:

**Scripted** (deterministic, what most regression scenarios use):

```yaml
- turn:
    input: "She gets a Slack ping at 9am summarizing overnight posts."
```

**Policy-driven** (the persona's behavior profile decides what to say given the prompt):

```yaml
- turn:
    policy: respond_to_journey_step
    constraints:
      mention: ["slack", "9am"]
      avoid_features: true                  # persona rule: no feature-shaped answers
```

Scripted form is for regression scenarios where determinism matters. Policy-driven is for exploratory runs where "a
methodical operator would say *something* satisfying these constraints." The simulator-operator agent reads its persona
profile + the constraints + the system's last prompt, generates a response.

## Persona library

Each persona is a YAML profile loaded as a system prompt for the simulator-operator agent. Initial 3-5:

```yaml
# jig/synthetic_operator/personas/methodical.yaml
id: methodical
description: |
  Carefully reads what the system prompts. Answers each question
  thoughtfully. Confirms gates only when they're clearly ready.
  Doesn't push back unless the system is obviously wrong.

response_patterns:
  - When asked about the product, gives a clear single-sentence pitch
    in domain language.
  - When asked about a persona, names a specific role with concrete
    reason ("engineering manager of a 6-person remote team," not "users").
  - When asked about a journey step, describes one concrete moment
    chronologically; doesn't skip ahead.
  - When the agent reads back a Phase-5 playback, reads it carefully,
    confirms or specifies a correction.

gate_confirmation_policy: confirm_when_clear
override_probability: 0.05                  # rarely overrides agent decisions
ambiguity_in_answers: low                   # answers are concrete and specific
patience_for_clarification: high            # answers follow-up questions willingly

avoid_behaviors:
  - feature_shaped_answers                  # never says "a dashboard with charts"
  - skip_ahead                              # doesn't jump to step 3 when on step 1
  - vague_confirmations                     # never says "yeah, fine, whatever"
```

```yaml
# jig/synthetic_operator/personas/fast_and_shippy.yaml
id: fast_and_shippy
description: |
  Wants to ship. Skips clarification. Confirms gates fast even when
  half-formed. Pushes back when the system asks for more detail.

response_patterns:
  - Single-sentence answers; minimal elaboration.
  - "Yes, fine, move on" to playbacks even when slight inaccuracies present.
  - Asks the agent to "just propose something" rather than walking
    through detail.

gate_confirmation_policy: confirm_eagerly
override_probability: 0.15                  # overrides "this needs more discussion" prompts
ambiguity_in_answers: medium
patience_for_clarification: low

avoid_behaviors:
  - long_explanations
```

```yaml
# jig/synthetic_operator/personas/scope_creeper.yaml
id: scope_creeper
description: |
  Adds requirements mid-discovery. "Oh and it should also..." midway
  through journeys. Tests whether the workflow handles late additions.

response_patterns:
  - Mid-journey, introduces a new persona or capability.
  - Suggests new features when the agent asks about behaviors.
  - When a playback comes back, mentions one thing that wasn't in
    the conversation ("oh wait, also it should send an email").

gate_confirmation_policy: confirm_then_re_open
override_probability: 0.10
ambiguity_in_answers: medium
patience_for_clarification: medium
```

```yaml
# jig/synthetic_operator/personas/ambivalent.yaml
id: ambivalent
description: |
  Doesn't have strong opinions. "Whatever you think." "Sure, I guess."
  Tests the workflow when the operator provides minimal signal.

response_patterns:
  - "Sure", "Yeah, I guess", "Whatever sounds good" to most prompts.
  - Defers to agent suggestions for personas, capabilities, contracts.
  - Confirms gates without much engagement.

gate_confirmation_policy: confirm_passively
override_probability: 0.02
ambiguity_in_answers: high
patience_for_clarification: low

avoid_behaviors:
  - specific_disagreement                   # never says "no, that's wrong"; says "I dunno"
```

```yaml
# jig/synthetic_operator/personas/hostile.yaml
id: hostile
description: |
  Adversarial. Disagrees often. Provides bad inputs. Tests that the
  workflow handles operator hostility gracefully — refusing without
  crashing, escalating without blame, gating without coercion.

response_patterns:
  - Disagrees with playbacks ("no, that's not what I said").
  - Provides contradictory inputs across turns.
  - Refuses gate confirmation without explanation.
  - Sometimes types junk inputs ("asdfasdf", "what?", "go away").

gate_confirmation_policy: refuse_initially
override_probability: 0.30
ambiguity_in_answers: high
patience_for_clarification: very_low

avoid_behaviors:
  - cooperation                             # disagrees by default
```

## Driver

`jig/synthetic_operator/driver.py` is the harness. Per-scenario lifecycle:

```
1. SETUP
   - Create isolated workspace: temp dir, fresh `.jig/` state
   - Spawn dedicated daemon instance against that workspace
   - Tag the daemon: env JIG_SIMULATOR=true so emitter sets `simulator: true`
     on every analytics event from this daemon

2. CONNECT
   - Open WebSocket to the spawned daemon
   - Subscribe to all topics
   - Initialize a synthetic-operator agent with the persona profile

3. PLAY
   For each turn in scenario.script:
     - Wait for system to be ready for input (idle or prompt-request)
     - If turn.input is scripted: send literal input to Composer
     - If turn.input is policy-driven: ask synthetic-operator agent to
       generate input given current system state + persona + constraints
     - Send input via daemon command
     - Wait for system response (with timeout)
     - Run per-turn assertions against post-response state
     - Record turn outcome to transcript.jsonl

4. FINAL
   - Run final_assertions against end-of-run state
   - Capture .jig/ state snapshot to artifacts/
   - Compute coverage delta (which paths were exercised this run)
   - Compute cost summary

5. TEARDOWN
   - Drop daemon instance
   - Drop workspace (or archive if scenario failed)
   - Write report.yaml

6. REPORT
   - Pass/fail per assertion
   - Cost summary
   - Coverage delta
   - Failure trace if any
```

## Assertion framework

Assertions are typed (Pydantic discriminated union). Each kind has its own check function. Common ones:

| Kind | Checks |
|---|---|
| `agent_spawned` | Within timeout, an agent of `role` was spawned. |
| `agent_completed` | Within timeout, an agent of `role` completed with `status`. |
| `artifact_exists` | A file path exists. Optional `contains: <substring>`. |
| `artifact_contains` | File at path contains anchor / pattern / structured field value. |
| `artifact_count_matches` | Glob pattern resolves to N files. |
| `analytics_event_emitted` | Event of `event_kind` was emitted within turn. Optional field-value matchers. |
| `analytics_event_NOT_emitted` | Negative — event was NOT emitted (catches silent failures). |
| `workflow_gate_reached` | Named workflow gate (po_l1_done, sa_done, etc.) was confirmed. |
| `cost_under` | Cumulative cost under `usd`. |
| `no_orphan_dev_resources` | No leftover dev-environment namespaces post-cleanup. |
| `no_unhandled_errors` | No exception trace in event stream. |
| `no_silent_failures` | No critical event without corresponding resolution event. |
| `tier_promotion_count` | Number of tier promotions observed equals expected. |
| `escalation_count` | Number of escalations of given type equals expected. |

Adding new assertion kinds is straightforward — extend the union, implement the check function. Per-scenario authors
compose from the available kinds.

## Coverage metrics

Each scenario tags itself with `coverage_tags:` (free-form strings naming the workflow paths it exercises). The
aggregate coverage view answers:

- **Path coverage**: across all scenarios, which `coverage_tags` are exercised? Surfaces tags claimed by zero scenarios
  — workflow paths nobody's tested.
- **Tier coverage**: which dev/reviewer tier combinations get exercised? Surfaces tier combinations that no scenario
  triggers.
- **Persona × stage coverage**: which (persona, workflow-stage) pairs get exercised? Surfaces gaps like "no ambivalent
  operator has been driven through SA discovery."
- **Auto-escalation trip coverage**: which auto-escalation thresholds (per pm-workflow design) have actually been
  tripped by a sim run? Untested thresholds may have wrong values.

Coverage report regenerated nightly. Operator reviews periodically; gaps flagged become candidates for new scenarios.

## Realism budget

The headline risk is shipping a system that passes every scenario but breaks immediately when a real operator behaves in
a way the simulator didn't anticipate. The realism budget is the discipline that prevents this:

- Every time a real operator hits a behavior the simulator wouldn't have produced (different input style, unexpected
  gate response, mid-stream behavior change, etc.), log it via `/realism log` slash command in the TUI. Captured in
  `.jig/sim/realism/gaps.jsonl` with a structured shape:

```yaml
  - id: rg-2026-05-15-01 logged_at: 2026-05-15T14:32:00Z real_operator_behavior: | Operator confirmed an L1 playback
    with a blank "yeah" — system treated as approval, but operator meant "I don't know."
    simulator_persona_that_should_cover: "ambivalent (extend with ambiguous-confirmation-pattern)" workflow_stage:
    po_l1_phase5_playback severity: notable                       # critical | important | notable status: open
    # open | covered | wontfix
```

- Periodic review (weekly?): operator triages open realism gaps. Each gets one of:
  - **Cover**: extend persona profile or add a new scenario; mark `covered`.
  - **Won't fix**: behavior is too rare to bother with; mark `wontfix` with rationale.
  - **Defer**: leave open, revisit later.

- **Realism-divergence metric**: ratio of `open + covered` realism gaps over time. Rising rate = simulator falling
  behind reality; falling rate = simulator catching up. Healthy systems show oscillation; consistent rise means the
  simulator isn't being tended to.

## CI integration

Three tiers, run at different cadences:

| Tier | When | Cost ceiling | Typical scenarios |
|---|---|---|---|
| **smoke** | Every PR | < 10 min, < $5 | Happy paths, regression for recent design changes (~10 scenarios) |
| **full** | Nightly | < 1 hour, < $50 | Smoke + edge cases, cross-persona variants (~50 scenarios) |
| **nightly** | Weekly | < 8 hours, < $300 | Full + cascade workflows, long-running multi-cycle scenarios (~150 scenarios) |

Failure of any smoke scenario blocks PR merge. Full / nightly failures surface as alerts; don't block, but get triaged.

## Analytics tagging

The synthetic-operator daemon sets `JIG_SIMULATOR=true` env var when spawned by the driver. The `EventEmitter` reads
this at startup:

```python
class EventEmitter:
    def __init__(self, store, simulator_mode: bool = None):
        self._store = store
        self._simulator_mode = (
            simulator_mode if simulator_mode is not None
            else os.environ.get("JIG_SIMULATOR") == "true"
        )

    async def emit(self, event):
        if self._simulator_mode:
            event = event.model_copy(update={"simulator": True})
        return await self._store.append(event)
```

Every event emitted from a simulator-mode daemon carries `simulator: true`. Consumer queries (analytics views,
dashboards, calibration loops, retrospective summarization) filter to `simulator: false` by default; simulator events
live in their own logical corpus. The base event class gets `simulator: bool = False` added.

## Synthetic-operator agent

The agent driving operator-side responses is itself a Claude Code subprocess (or equivalent), spawned with:

- **Role**: `synthetic-operator`
- **Model**: small / cheap (haiku-tier) — the operator role is mostly response-generation, not deep reasoning.
- **System prompt**: the persona profile + scenario context + per-turn constraints.
- **Tool surface**: minimal — read system state via WebSocket events, send responses via daemon command. No file-system
  access, no git, no bus-publish. Strict tools.
- **Context budget**: tight — only the persona, scenario, and current system state. Prior turns summarized.

This means the simulator-operator agent itself is just another role in jig, spawned and managed by the same
infrastructure. Tenet 2 (exact context) applies — the simulator-operator agent operates exactly like a real operator's
mental model, with the same operator-facing surface and no privileged access.

## TUI vs daemon-API drive

Two ways the driver can send operator input to the daemon:

- **Daemon API (default)**: driver invokes daemon commands directly (`prompt_reply`, `command`, etc.). Fastest, most
  deterministic. Validates daemon-side workflow logic without TUI-rendering details.
- **TUI-driving (opt-in)**: driver spawns a Textual TUI process, programmatically types into the Composer. Validates TUI
  rendering + interaction. Slower, more brittle (Textual snapshot/interaction harness needed).

Default to daemon-API for cost and reliability; TUI-driving for scenarios that specifically test TUI behavior (operator
clicks tabs, paste image, etc.).

## Failure-mode regression scenarios

Beyond happy-path coverage, the scenario library carries explicit regression scenarios for each known failure mode. When
a real bug surfaces, the fix lands with a regression scenario that would have caught it. Examples:

- `regression-cascade-overlap.scenario.yaml` — two spikes complete simultaneously with overlapping cascade proposals;
  system should serialize.
- `regression-bones-doesnt-converge.scenario.yaml` — SA delta-amends fail twice; system should escalate to operator with
  structured trace.
- `regression-orphan-dev-namespace.scenario.yaml` — agent crashes mid-ticket; cleanup should fire on next daemon start,
  marking namespace as orphan candidate.

Convention: regression scenarios are prefixed `regression-` and live in their own subdirectory; CI fails if any
regression scenario passes when the bug is reintroduced (literal "this should fail; alarm if it doesn't").

## CLI

```bash
# Run one scenario
jig sim run docs/v2.0/synthetic-operator/scenarios/smoke/po-l1-happy-path.scenario.yaml

# Run all smoke scenarios in parallel (with isolation)
jig sim run-tier smoke

# Run nightly tier with explicit cost cap
jig sim run-tier nightly --max-cost 200

# View aggregate coverage report
jig sim coverage

# Show realism-budget status
jig sim realism status

# Log a real-operator surprise as a realism gap
jig sim realism log --workflow-stage po_l1_phase5_playback --severity notable
```

`jig sim` becomes a peer of `jig daemon` / `jig story` / `jig build` — first-class CLI surface for simulation
operations. Inside the TUI, `/sim` slash commands mirror the CLI.

## Risks (of this design)

- **Authoring cost**. Scenarios are tedious to hand-write. Mitigation: ship a small initial library focused on
  load-bearing paths; expand as bugs surface (via regression scenarios) and as realism budget grows.
- **Persona profiles are subjective**. Two readers may disagree on what "methodical" means. Mitigation: structured
  behavior profiles (response patterns + policies + probabilities + avoid-behaviors) make personas testable rather than
  vibe-based; author personas based on real operator patterns observed in production.
- **Cost overruns**. Nightly tier could blow the budget. Mitigation: hard cost caps per scenario + aggregate cap per
  tier; cost report at end of each run; alerts if a scenario consistently runs near its cap.
- **False-failure thrashing**. Flaky scenarios (intermittent failures from non-determinism) erode trust. Mitigation:
  every scenario has a `retry_count` (default 0); flaky scenarios get logged + investigated + either fixed or marked
  unstable; no PR-blocking on unstable tier.
- **Drift from real-operator behavior**. The simulator personas converge on the patterns the author imagined, not what
  real operators actually do. Mitigation: the realism budget is the discipline; tracked metric of divergence; periodic
  operator review.

## Out of scope

- LLM-generated scenarios from project archetypes (v2.x; full agent-driven testing in `docs/v2.0/agent-testing/`).
- Simulating non-operator failure modes (network, LLM API outages, disk-full). Resilience testing is separate.
- Multi-operator collaboration scenarios.
- Replacing pytest unit tests for code-level correctness.
- Chaos / fault-injection testing — explicit non-goal at this scale.
- Performance / load testing.

## Open questions

(continued from problem.md, plus new ones)

- [ ] **Daemon spawn cost vs reuse.** Spawning a fresh daemon per scenario is clean but slow (~5 sec daemon startup ×
  150 scenarios = 12 min just in startup). Reusing a daemon with namespace isolation per run is faster but risks
  cross-run pollution. Probably acceptable for v2 to spawn fresh; revisit if startup cost dominates.
- [ ] **How are personas validated against real operators?** Periodic review of recent real-operator behavior vs persona
  profile coverage; gaps logged as realism budget items. But who does this review? Probably operator themselves; could
  be a quartermaster-agent task once that lands.
- [ ] **Should scenarios be Pydantic-validated at load time?** Yes — schema in `jig/synthetic_operator/scenario.py` with
  strict validation; bad scenarios fail fast.
- [ ] **Where do scenario coverage tags get standardized?** Free-form strings is loose; risk of typos diluting coverage.
  Probably a `coverage_tags.yaml` taxonomy file in the simulator package; new tags need to be added there before
  scenarios reference them; linter enforces.
- [ ] **Cross-scenario state**. Do scenarios always start fresh, or can they chain (e.g., "scenario B picks up where
  scenario A ended")? Probably start fresh; chained scenarios are a v2.x extension if needed.

## Implementation phases

Aligned with v2 build phasing (parallel-with-build per agent-leverage doc):

1. **Scenario schema + driver skeleton** — Pydantic schema for scenarios, basic driver that spawns fresh daemon
   + plays scripted turns + captures pass/fail. Lands early so subsequent v2 work has a place to add scenarios.
2. **Synthetic-operator agent + first persona (methodical)** — agent role with persona profile loading; runs scripted
   turns deterministically.
3. **First smoke scenarios** — 3-5 happy-path scenarios for load-bearing v2 paths (PO L1 happy, SA cascade happy, PM
   bones-first happy). Validates the driver works.
4. **Per-run isolation primitives** — fresh workspace per run, daemon teardown, artifact archive on failure.
5. **Assertion framework** — Pydantic union of assertion kinds + check functions; extensible.
6. **Analytics tagging** — `simulator: true` field on event base; emitter respects `JIG_SIMULATOR` env var; consumer
   queries filter accordingly.
7. **Additional personas** — fast-and-shippy, scope-creeper, ambivalent, hostile.
8. **Policy-driven turns** — the synthetic-operator agent generates responses given persona + constraints + system
   state.
9. **Coverage metrics** — coverage_tags taxonomy + aggregate report.
10. **Realism budget** — `/realism log` command, gaps.jsonl persistence, periodic-review affordance.
11. **CI integration** — smoke/full/nightly tiers with cost caps and PR-blocking on smoke.
12. **TUI-driving mode** — for the scenarios that specifically need it.
13. **Regression scenario discipline** — convention + linter ensuring every bug fix lands with a regression scenario.

Phases 1-6 are the minimum viable simulator and should land early in v2 build. Phases 7-13 land progressively as the v2
build expands and scenarios accumulate.

## Change log

- 2026-05-03: Initial design (brent + claude). Synthetic-operator agent role + scenario YAML format + driver
  + assertion framework + persona library (initial 5: methodical, fast-and-shippy, scope-creeper, ambivalent,
hostile) + coverage metrics + realism budget + CI tiering + analytics tagging via `simulator: true` event field. Per-run
isolation via fresh daemon + workspace. CLI surface as `jig sim ...`; mirrored in TUI as `/sim` slash commands. Scripted
turns for regression; policy-driven turns for exploratory runs. 13-phase implementation plan; first 6 phases are
v2-build-early.
