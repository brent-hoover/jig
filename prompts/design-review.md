# Design Review Prompt

You are reviewing the design at `[plans](../feature-work/plans)<doc-location>` against the `problem.md` in
the same directory. Follow this process exactly. Do not improvise the structure.

The design doc is the most consequential artifact in the workflow: a wrong
problem statement wastes a design, a wrong plan wastes an implementation, but
a wrong design wastes everything downstream and is the hardest to undo. Review
accordingly — pedantic about compliance, ruthless about smuggled assumptions,
slow to wave anything through.

## Inputs (fill in before pasting)

- **Design path:** `feature-work/<feature>/design.md`
- **Problem path:** `feature-work/<feature>/problem.md` (authoritative for
  requirements, non-goals, success criteria)
- **Specs:** `feature-work/<feature>/specs/` if any pre-exist (REQ-IDs the
  design must respect or implement)
- **Adjacent designs:** other `feature-work/<feature>/design.md` documents
  the design touches, depends on, or contradicts
- **Existing ADRs:** anything in `docs/adrs/` the design must respect
- **Blast radius:** what production surfaces the eventual implementation
  will touch (gateway path, models namespace, dashboard, CI gate, security
  boundary, data store, etc.)
- **Out of scope for review:** prose quality, formatting, frontmatter —
  unless required frontmatter fields are missing or `status` is wrong, or
  the `problem:` link is missing/broken

If `problem.md` is missing, in `draft` status, or describes a different
feature, stop and say so. A design reviewed against an unapproved problem is
noise — and a design that drifts from the problem is the single most common
failure this prompt is built to catch.

---

## Project context the reviewer must hold

Before reviewing, internalize these from `CLAUDE.md`,
`docs/SYSTEM-ARCHITECTURE.md`, and `docs/PROJECT_REFERENCE.md`. The constitution
is load-bearing — a design can read beautifully and still be wrong if it
violates one of these.

- **Two choke points, no exceptions.** Designs that introduce a third
  interception point, a bypass, a "fast path" around the gateways, or a new
  control surface that isn't a verifier subsystem hanging off an existing
  choke point, violate the constitution. This is the single most common
  smuggle in design docs.
- **Binary verdicts only.** No "scoring", "warning levels", "soft enforcement",
  "advisory mode" in safety controls. Discovery and Simulation modes are
  observability modes — they don't make the verdicts non-binary, they just
  don't enforce them.
- **Verifiers cannot act.** A design where a verifier writes state, mutates
  config, or remediates is wrong. Verdicts go to choke points; choke points act.
- **Replay is mandatory.** Any design that produces verdicts must produce
  replay fixtures. A design that defers replay is deferring a constitution
  obligation, not a feature.
- **Observed behavior > declared intent.** A design that relies entirely on
  manifests / declarations / config without an observation channel is half
  a design.
- **Reachability defines risk.** A design that flags risks without considering
  whether they're reachable in the production graph is over-rating.
- **OWASP coverage is a CI gate.** Designs that touch scenario coverage must
  preserve or extend it.
- **Canary hits are hard failures, zero false positives.** Designs that ship
  canaries with review queues, score thresholds, or deduplication windows are
  wrong.
- **No silent failure.** A design where a path can fail without an emitted
  trace span / verdict is wrong.

A design that, taken at face value, requires violating any of these is itself
a Critical finding regardless of how clean the rest reads.

---

## Process (in order — do not skip)

1. **Read the problem once for intent.** Before opening the design, summarize
   in 2–3 sentences what must be solved (from `problem.md`): the broken/missing
   thing, the requirements, the non-goals, the success criteria. If you can't,
   the problem isn't approved-quality and the design can't be reviewed against
   it — stop and say so.
2. **Extract obligations.** List, from `problem.md`:
   - Each requirement (must-be-true).
   - Each non-goal (must-not-be-in-scope).
   - Each success criterion (observation that proves it solved).
   - Any REQ-IDs from `specs/`.
   - Any ADRs the work must respect.
   This is your checklist. The design will be measured against it.
3. **Read the design once for approach.** Summarize the chosen approach in
   2–3 sentences before flagging anything. If your summary disagrees with
   the design's own §Summary or §Approach, that gap is itself a finding.
4. **First pass — coverage.** For each obligation from step 2, locate where
   the design satisfies it. Mark each: covered (which section), partially
   covered, missing, or contradicted. Anything missing or contradicted is a
   finding.
5. **Second pass — constitution.** Walk the project context list above.
   For each principle, ask: *"Does this design respect it, or does the
   approach (or any of its components) require violating it?"* Be especially
   adversarial about three-choke-point smuggles — they often hide as
   "preprocessor", "fast path", "out-of-band check", or a new admission
   webhook that duplicates gateway responsibility.
6. **Third pass — alternatives rigor.** This is the most valuable section of
   any design doc. Check:
   - Is the obvious naive approach considered and rejected with concrete
     reasoning, or is it skipped?
   - Is each rejection reason a real cost (a measurable downside) or a
     hand-wave ("doesn't scale", "harder to maintain")?
   - Is the chosen approach's rationale a positive case ("wins because…")
     or only a negative case ("the others were worse")?
   - Are there obvious alternatives the design didn't consider? Name them.
   A design with a perfunctory Alternatives section is a High finding even
   if the chosen approach happens to be right — because the *next* design
   reviewer (six months from now, asking "why didn't we just…") has no
   answer.
7. **Fourth pass — interface pinning.** For each interface in §Interfaces
   (APIs, CLI surfaces, file formats, wire protocols, OTel attribute names,
   trace span shapes), ask: *"Could a competent engineer write a plan
   against this without making a major decision?"* If interfaces are
   underspecified, the plan reviewer will catch it later — but it's
   cheaper to catch here.
8. **Fifth pass — data model.** If §Data model exists, check:
   - Identity / keying — what's the primary identity, and is it stable
     across renders/rewrites?
   - Migration story — if this replaces or extends existing state, how does
     existing data move? Is there a backfill?
   - Concurrency — who writes, who reads, what's atomic?
   - Schema evolution — how does this shape change in the future without
     breaking readers?
   Skip this pass if the design has no persistent state.
9. **Sixth pass — risks honesty.** Walk §Risks. For each risk, ask:
   - Is this a real risk (concrete failure mode tied to this design's
     specific choices) or boilerplate ("performance", "complexity")?
   - Is the blast radius named?
   - Is mitigation either described or explicitly deferred to runtime?
   A thin or boilerplate Risks section is a finding — risks the author
   didn't see are the ones that bite.
10. **Seventh pass — scope drift.** Compare the design's §Out of scope and
    its actual approach against the problem's non-goals and requirements:
    - Has the design quietly expanded scope beyond what the problem asked
      for? (Adding a feature the problem didn't request.)
    - Has the design quietly *contracted* scope below what the problem asked
      for? (Solving a smaller version of the problem and calling it solved.)
    - Has the design adopted any non-goal from `problem.md` as in-scope?
    Both directions are findings.
11. **Eighth pass — deferral audit.** Walk the entire design looking for
    anything marked **deferred**, **for later**, **future work**, **v2**,
    **out of scope for now**, **follow-up**, **TBD**, **post-MVP**, or any
    equivalent phrasing — including in §Out of scope, §Open questions,
    inline comments in §Approach, or hand-waves in §Interfaces / §Data model
    ("we'll figure out the schema migration later"). For each deferred item,
    answer:
    - *"If this design ships exactly as written and the deferred item is
      never built, would the problem be solved per `problem.md`'s success
      criteria and requirements?"*
    If the answer is no, the deferral is **critical work, not later work**,
    and the design is shipping an incomplete approach under cover of a
    "future" label. Flag it. Treat especially harshly:
    - Deferrals that hide a constitution obligation (e.g., "replay fixture
      generation deferred to v2" — replay is mandatory, this is not a
      deferral, it's a violation).
    - Deferrals tied to a problem requirement (e.g., problem says "must
      detect injection" and design says "injection detection deferred").
    - Deferrals that load-bear on a downstream success criterion.
    - Deferrals named only in passing (one-line mention, no follow-up doc
      reference) — these are most often forgotten.
    A design is allowed to defer work; it is not allowed to defer the work
    that makes the problem solved.
12. **Ninth pass — open questions triage.** Classify each open question:
    - **Blocking** — must be answered before plan starts.
    - **Plan-time** — should be answered while writing the plan, not now.
    - **Implementation-time** — belongs in the code, not here.
    Mis-classification (especially blocking questions left as "plan-time") is
    a finding. A blocking question left in an `active` design is itself a
    Critical finding — the design isn't ready.
13. **False-positive guard.** For each finding, try to invalidate it by
    re-reading the problem and design. If the design explicitly addresses
    your concern and you missed it (often in §Alternatives or §Risks), drop
    the finding. **If you cannot point to a concrete downstream consequence
    (wrong implementation, dropped requirement, expensive interface change,
    constitution violation in production) of leaving the design as-is,
    demote confidence or drop it.**
14. **Calibrate.** Ask: *"If I reviewed 10 designs of similar size with this
    prompt, how many would have a Critical?"* If your answer is more than
    1–2, you are over-rating. Re-rank. Designs are *expected* to have at
    least Medium findings — if you have none, you didn't look hard enough.

---

## Severity rubric

Severity = impact **if this design is approved as-is and a plan + implementation
are built against it**, given the stated blast radius. Severity is not "how
bad does this look" — it's "what is wrong, missed, or expensive to undo
downstream."

- **Critical** — Design violates a constitution principle (two choke points,
  binary verdicts, replay mandatory, verifiers don't act, no silent failure,
  OWASP coverage, canary discipline); fails to satisfy a `problem.md`
  requirement with no acknowledgment; adopts a `problem.md` non-goal as
  in-scope; ships an unobservable success criterion (the design's "done"
  state can't be verified); **defers work that the problem's success
  criteria or requirements depend on, under any "later / v2 / future / TBD"
  label**; defers a constitution obligation (replay, OWASP coverage, canary
  discipline) under any label; leaves a blocking open question unresolved
  while marked `active`; pins an interface that is known-wrong (contradicts
  an ADR or an existing production interface). Justifies blocking design
  approval.
- **High** — Alternatives section is perfunctory or skips the obvious naive
  approach (the "why didn't we just…" question is unanswered); interfaces
  underspecified to the point a plan cannot be written against them without
  making a major decision; risks section is boilerplate so blast radius is
  unconsidered; design contradicts problem's non-goals with an
  acknowledgment but no resolution; data model lacks an identity/keying
  story for stateful entities; design relies on declared intent only when
  observed behavior is needed; defers a piece of work that is not strictly
  required for the problem but is named as a follow-up with no owner, no
  follow-up doc reference, and no integration plan (the kind of deferral
  that quietly becomes permanent).
- **Medium** — Data model has a structural smell that's recoverable
  (e.g., concurrency story missing on a single-writer path); an interface
  choice will be expensive to change later but isn't justified; out-of-scope
  thin in a non-trivial design; alternative considered but rejected with a
  hand-wave; risks named but mitigation neither described nor deferred.
- **Low** — Minor wording, redundant section, success-criterion observable
  but cumbersome.
- **Info** — Frontmatter, doc location, naming, change log, broken `problem:`
  link. No design-time consequence.

**Hard rule:** if you cannot name a concrete downstream consequence (wrong
implementation, dropped requirement, expensive interface change, constitution
violation in production), the finding is **not** Critical and **not** High.

## Confidence rubric (orthogonal axis)

- **Confirmed** — The gap or violation is fully traceable to text in
  `problem.md` / `design.md` / `specs/` / project context.
- **Likely** — Strong evidence in the docs; minor uncertainty about author
  intent or about whether the concern is addressed elsewhere.
- **Possible** — Plausible from the design's shape, not fully verifiable
  without conversation with the author or context outside these docs.
- **Speculative** — Worth investigating; flagging an area, not claiming a defect.

A High-severity Speculative finding is a *question*, not a verdict. Mark it as such.

---

## Finding format

```
### [Severity:Confidence] Short title
- Location: design.md §<section>
- Category: problem-compliance | constitution | alternatives | interfaces | data-model | risks | scope-drift | deferral | open-questions
- What the design says: brief quote or paraphrase
- What the problem/constitution requires: the specific obligation being missed or violated
- Consequence: what bad outcome propagates downstream if a plan / implementation is built against this as-is
- Fix sketch: minimum change to the design that addresses the root cause
```

---

## Required output sections (in this order)

1. **Intent summary** — from step 1 and step 3. Two paragraphs: what the
   problem requires, what the design proposes. If they don't line up, say so
   here.
2. **Obligation checklist** — from step 2. Each problem-requirement,
   non-goal, and success criterion mapped to the design section that
   handles it (or marked missing / contradicted).
3. **Constitution audit** — from step 5. One line per principle: respected,
   at-risk (with the specific section), or violated.
4. **Alternatives assessment** — from step 6. Is §Alternatives doing real
   work, or is it perfunctory? Name any obvious alternative the design
   didn't consider.
5. **Interface readiness** — from step 7. Could a plan be written against
   §Interfaces as-is? If not, what's underspecified?
6. **Deferral audit** — from step 11. Bullet list of every deferred /
   for-later / v2 / future / TBD / post-MVP item in the design, each
   classified as: **safe to defer** (problem is solved without it),
   **load-bearing** (problem is not solved without it — must move into
   scope), or **constitution-deferred** (a mandatory obligation deferred,
   which is a violation regardless of label). Empty list is a valid
   (and good) result; treat "I didn't see any" as a separate, weaker claim
   than "I looked and there are none."
7. **Findings** — ordered by Severity then Confidence.
8. **Checked and clean** — areas you actively verified and have no concerns
   about (e.g., "data model identity is stable across re-renders";
   "alternative §X considered with concrete cost reasoning"). Reduces
   back-and-forth.
9. **Couldn't verify** — what you'd need to see for a complete review:
   adjacent designs, prior ADRs, stakeholder context, the unbuilt
   subsystem this depends on.
10. **Recommended fix order** — if findings depend on each other, the order
    to address them and why.

---

## Anti-patterns — do not do these

- Do not propose a different design. The design is what's being reviewed —
  your job is to make this design correct, not to replace it. Exception: in
  §Alternatives assessment, naming an obvious-alternative-not-considered is
  on-task; sketching your own design isn't.
- Do not flag "consider documenting more" without naming the specific
  decision the doc fails to capture and the consequence of missing it.
- Do not flag every prescriptive sentence in the design as over-specification —
  designs are *supposed* to specify. The test is "does this lock something
  the plan should be free to choose", not "is this prescriptive."
- Do not flag risks that the design didn't list unless they're reachable in
  this design's specific approach. Generic risks are noise.
- Do not pad. A review with one Critical constitution violation and a clean
  rest is more valuable than twelve Mediums and no signal.
- Do not mark anything Critical without naming the constitution principle,
  the missing requirement, the contradicted non-goal, or the unobservable
  success criterion.
- Do not paraphrase the design back at me. Assume I can read it.
- Do not flag formatting, prose, or frontmatter unless required fields are
  missing, `status` is wrong, or the `problem:` link is missing/broken.

---

## Example findings (format calibration)

### [Critical:Confirmed] Approach introduces a third interception point
- Location: design.md §Approach (Component: "Egress Sentinel")
- Category: constitution
- What the design says: "The Egress Sentinel runs as a CoreDNS plugin and
  intercepts outbound DNS queries from agent pods, producing a verdict before
  the request reaches the Tool Gateway. Verdicts are emitted to OTel and
  consulted by the gateway."
- What the problem/constitution requires: `CLAUDE.md` and
  `docs/SYSTEM-ARCHITECTURE.md` pin the architecture at exactly two choke
  points (Tool Gateway, Model Gateway). The Egress Sentinel is a third
  interception point in the request path — it produces verdicts that flow
  into enforcement decisions, which is precisely the choke-point role.
- Consequence: A plan built against this design will ship a third
  interception point in production. Either the constitution principle gets
  silently weakened, or the design has to be redone after the plan is half
  written. Independently, "verdicts are consulted by the gateway" creates a
  second-order coupling that makes replay nondeterministic — Sentinel
  verdict ordering becomes part of the input.
- Fix sketch: Move the egress check inside the Tool Gateway as a verifier
  subsystem (not a separate interception point), with the same
  pre/post hooks the other verifiers use. If DNS-layer interception is
  genuinely required, escalate as an ADR proposing to amend the
  two-choke-point principle — don't smuggle it through a design doc.

### [Critical:Confirmed] Load-bearing work deferred under "v2" label
- Location: design.md §Out of scope ("PII redaction in MCI deferred to v2")
- Category: deferral
- What the design says: Under §Out of scope, "PII redaction in the Model
  Content Inspector is deferred to v2; v1 will detect-and-emit-verdict only,
  no redaction of the request payload before forwarding."
- What the problem/constitution requires: `problem.md` success criterion #2
  states "PII detected in an LLM request must not reach the upstream model
  provider." Detection alone does not satisfy this — without redaction (or
  blocking), the PII still reaches the provider. The success criterion is
  unsatisfiable by the design as written.
- Consequence: A plan and implementation built against this design will ship
  a v1 that cannot pass its own success criterion. Either v1 is declared
  done while violating the criterion (silent failure of the acceptance test),
  or v1 is shipped and immediately needs the v2 work, meaning the deferral
  bought nothing and cost a round trip through design.
- Fix sketch: Either (a) move PII redaction into v1 scope and into §Approach,
  or (b) escalate `problem.md` success criterion #2 — if v1 genuinely is
  detection-only, the success criterion needs to be amended in the problem
  statement first, not silently weakened by the design. Option (b) requires
  problem.md to go back to draft.
