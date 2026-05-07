# Integration-Coherence Plan

A sequenced plan for the items pulled from
``coherent-whole-techniques.md`` and
``product-definition-improvements.md``, plus an evaluation harness so
we can tell which changes actually move outcomes.

The dependency graph (specified separately in
``dependency-graph-spec.md``) is one of the items here, not the whole
plan.

## Goal

Make integration drift visible through multiple independent signals
before the operator discovers it by running the app manually. Each
phase below adds one such signal.

## Phase 0 — Evaluation harness (prerequisite)

Without a baseline you can't tell which later changes are net-positive.
Phase 0 lands first.

### 0.1 Model projects

Two projects so the eval surface covers both shapes jig is built for.
They're complementary and short enough that running both real-mode is
still tractable.

#### Project A: ``hn-cli`` — CLI + external API, no VD

A small command-line tool that prints Hacker News top stories.

- **Modules**: ``api-client``, ``cli-frontend``
- **Capabilities**: fetch-top-stories, filter-by-score, format-output
  (text / json)
- **External API**: HN Firebase, recorded into fixtures (replay-only by
  default)
- **VD**: not involved.
- **Tracer**: ``hn-cli top --limit 3`` → 3 lines from the fixture
  corpus.
- **Brief size**: ~2 suites, ~6 capabilities, ~8–10 tickets across
  bones + MVP.
- **Why this shape**: small enough to run real-mode in <30 min;
  exercises ``external_dependencies`` + fixtures; unambiguous pass/fail
  via the tracer.

#### Project B: ``recipe-browser`` — web + VD

A web app that reads markdown recipe files from disk and renders them
in the browser.

- **Modules**: ``recipe-loader`` (filesystem reader),
  ``web-frontend`` (templates + minimal JS)
- **Capabilities**: list-recipes, view-recipe-detail, filter-by-tag
- **External API**: none. Recipes are markdown files committed under
  ``content/recipes/*.md``.
- **VD**: required. Wireframes for the index page and the detail
  page; visual_compliance reviewer enforces them.
- **Tracer**: smoke that boots the app, fetches ``/``, asserts three
  recipe rows render; navigates to one, asserts the markdown body
  rendered.
- **Brief size**: ~2 suites, ~6 capabilities, ~10–12 tickets including
  VD-authored wireframes.
- **Why this shape**: zero external-API confound, fully self-contained;
  exercises the VD pipeline (wireframes → visual_compliance →
  responsive / accessibility reviewers); has unambiguous pass/fail via
  the smoke.

Briefs (L0 pitch + intended scope + tracer) are sketched under
``evals/projects/<id>/brief.md``.

### 0.2 Harness shape

```
jig eval run <project-id> [--label <name>] [--runs N]
  → .jig/eval/<project-id>/<run-id>/manifest.yaml
     git sha, jig version, timestamp, model, scenario_seed
     synthetic-operator prompt hash (pinned per run)
     all SystemEvents from the run (already in store)
     reviewer-comment counts by severity
     cost, duration, ticket-status counts, fix-cycle counts
     tracer smoke pass/fail + output diff
```

```
jig eval compare <project-id> --before <label|sha> --after <label|sha>
  → markdown table: metric, before, after, delta, simple-stats if N>3
```

Storage under ``.jig/eval/`` so it stays orthogonal to the project's
own state.

### 0.3 Two kinds of eval per change

Each plan item picks one or both:

1. **Aggregate eval** — does running the model project N times with
   the change show better metrics? Useful for spawn-context
   narrowing, reviewer targeting, tier promotion.
2. **Targeted scenario** — a planted brief that exercises the
   specific failure mode the change prevents. Useful for the tradeoff
   ledger (does the agent re-add deferred work?), integration
   checkpoint (does interface drift surface?), consumer-driven
   contract reviewer (does the missing-test flag fire?).

Targeted scenarios live alongside ``tests/scenarios/`` and run in
mock-mode in CI. Aggregate evals run real-mode on demand.

### 0.4 Sequencing inside Phase 0

1. Build the harness (``jig eval run/compare``, manifest format,
   metric collection).
2. Codify ``hn-cli`` as a brief + tracer + replay-fixtures.
3. Run baseline on ``main`` × 5. Capture variance per metric.
4. Set thresholds: a change passes if it improves the median by > X%
   AND doesn't widen variance by > Y%.
5. Each later plan item lands → re-run + targeted scenario →
   compare → keep or revert.

Step 5's revert option is load-bearing: the whole point is to filter
signal from ritual. Items that don't measurably help should not stay.

## Phase 1 — Product-definition artifacts (PO upstream)

These feed every later phase. Each is small.

1. **Tradeoff ledger** — ``.jig/spec/tradeoffs.yaml``; PO authors,
   reviewers consult to flag re-added deferred work.
2. **Layered "done-enough" per capability** — bones / MVP / final
   blocks on ``Capability``; PM consumes during planning.
3. **Example-first acceptance criteria** —
   ``examples: list[GivenWhenThen]`` on capability/ticket; reviewers
   and simulator consume.

Phase 1 can land in parallel with Phase 2.

## Phase 2 — Integration metadata (additive schema)

No behaviour change yet; everything later leans on these.

4. **Integration checkpoint at handoff** —
   ``new_public_surfaces / changed_assumptions / required_followups /
   unknowns`` on the ``Handoff`` thread entry.
5. **Graph schema additions** — ``Module.consumes_apis/events``,
   ``Ticket.touches``, ``ExposedAPI.kind`` extension to include
   ``route``, ``migration``, ``env_var``. ``arch_finalize`` gains
   cross-ref validation. (= dep-graph PR #1.)

## Phase 3 — Reviewers and queries built on the new metadata

6. **Consumer-driven contract test reviewer** — flags "contract has
   consumers but no provider/consumer test." Uses ``Module.consumes_*``
   from #5.
7. **Dependency graph derivation + types** — ``DependencyGraph``,
   ``Node``, ``Edge``, ``TicketImpact``, ``jig/graph/derive.py``,
   materialised snapshot. (= dep-graph PR #2.)
8. **Graph CLI + MCP tools** — ``jig graph *``, five MCP handlers
   wired into operational roles. (= dep-graph PR #3.)

## Phase 4 — Behavioural hookups (the payoff)

9. **Spawn-context narrowing** — graph-neighborhood loader behind a
   feature flag for before/after comparison. (= dep-graph PR #4.)
10. **Reviewer dispatch + tier promotion** — graph-aware reviewer
    selection; planner auto-promotes ``dev_tier`` on cross-boundary
    tickets. (= dep-graph PR #5.)
11. **Analytics + quartermaster** — ``TicketGraphImpact`` event;
    "complex tickets" pattern in briefings. (= dep-graph PR #6.)

## Phase 5 — Tracer-bullet preservation

12. **First-class tracer schema** — ``.jig/spec/tracers/<id>.yaml``
    decoupled from the bones ticket that birthed it.
13. **Tracer-preservation reviewer** — re-runs the bones smoke when a
    graph-touched node has an attached tracer. Depends on #5 + #7.

Cut Phase 5 from MVP if scope pressure hits — it's the most speculative.

## Notes on sequencing

- The "interface registry" idea from
  ``coherent-whole-techniques.md`` is folded into Phase 2 #5 via the
  ``ExposedAPI.kind`` extension — no separate registry artifact.
- Phase 3 strictly needs Phase 2 (#5 unlocks #6 and #7).
- Phase 4 is where most of the integration-drift payoff lands; the
  earlier phases are load-bearing setup.

## Sizing

- Phase 0: ~1 week (harness + first project + baseline runs).
- Phase 1: days each.
- Phase 2: days each.
- Phase 3: ~week each for #7 / #8; days for #6.
- Phase 4: ~week each.
- Phase 5: days each.

## Risks to budget for

1. **Variance drowning small effects.** Some Phase 1 items likely have
   small aggregate effects on a 5-run sample. Pair the aggregate eval
   with targeted scenarios.
2. **The synthetic operator is a confound.** Improvements to its prompt
   move the baseline. Pin its prompt/version per eval run.
3. **Cost is fine** (Max subscription) but real-mode runs still take
   wall-clock time — keep the model project small.

## Relationship to other docs

- ``dependency-graph-spec.md`` — full spec for items #5, #7-#11.
- ``coherent-whole-techniques.md`` — source for items #4-#13.
- ``product-definition-improvements.md`` — source for items #1-#3.
- ``v2-review-findings-{security,silent-failures,type-design}.md`` —
  prior-pass findings, already landed in ``c0eb656``.
