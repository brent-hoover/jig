# Plan Review Prompt

You are reviewing the plan at `/Users/brent/Projects/personal/jig/docs/pm-scheduling/plan.md` against the `problem.md` and
`design.md` in the same directory. Follow this process exactly. Do not improvise
the structure.

## Inputs (fill in before pasting)

- **Plan path:** `feature-work/<feature>/plan.md`
- **Problem path:** `feature-work/<feature>/problem.md` (authoritative for requirements & non-goals)
- **Design path:** `feature-work/<feature>/design.md` (authoritative for approach, interfaces, alternatives)
- **Specs:** `feature-work/<feature>/specs/` if present (REQ-IDs the plan should reference)
- **Blast radius:** what production surfaces this work touches (gateway path, models namespace, dashboard, CI gate, etc.)
- **Out of scope for review:** prose quality, formatting, frontmatter — unless the plan is missing required frontmatter fields

If `problem.md` or `design.md` is missing or in `draft` status, stop and say so.
A plan reviewed against an unapproved design is noise.

---

## Project context the reviewer must hold

Before reviewing, internalize these from `CLAUDE.md` and `TENETS.md`:

- **Tracer bullets, not horizontal phases.** Each tracer must be a thin
  end-to-end slice — gateway → verifier → trace store → dashboard, or whatever
  the feature's full path is — that is demoable on its own. A tracer named
  "phase 1: backend" is a category error.
- **Two choke points, no exceptions.** Plans that introduce a third interception
  point, a bypass, or a "fast path" violate the constitution.
- **Binary verdicts only.** No soft modes, warn levels, or scored outputs in
  safety controls. A plan that ships a "warning state" is wrong.
- **Replay is mandatory.** Anything producing a verdict must produce a replay
  fixture or a clear path to one.
- **Verifiers cannot act.** Plans that have a verifier mutate state are wrong.
- **OWASP coverage is a CI gate.** Any change touching scenario coverage must
  preserve or extend it, never silently weaken it.
- **Canary hits are hard failures.** Zero false positives. A plan that ships a
  canary with a "review queue" is wrong.

A plan that requires violating one of these to land is itself a Critical finding,
regardless of how clean the steps look.

---

## Process (in order — do not skip)

1. **Read the problem and design once for intent.** Before opening the plan,
   summarize in 2–3 sentences what the feature must do (from `problem.md`) and
   how the design proposes to do it (from `design.md`). If you can't, the design
   isn't ready and the plan can't be reviewed against it.
2. **Extract the obligations.** List the requirements from `problem.md` and the
   design decisions / interfaces from `design.md` that the plan must satisfy.
   Note any REQ-IDs from `specs/`. This is your checklist.
3. **First pass — coverage.** Walk the plan top to bottom. For each obligation
   from step 2, mark which step(s) deliver it. Anything unmatched is either a
   gap or out-of-scope-but-undeclared.
4. **Second pass — tracer integrity.** For each tracer in the plan, ask:
   - Does it cross every layer the feature actually touches, or does it stop
     at a layer boundary?
   - Is it demoable on its own — i.e., would a stakeholder see something real
     work end-to-end after this tracer ships?
   - Is the split natural, or does it force redoing work in the next tracer
     (e.g., scaffolding thrown away, interfaces changed, tests rewritten)?
   - Does it produce a verdict / artifact / fixture the next tracer can build on?
5. **Third pass — deferral audit.** List everything the plan defers, calls
   out-of-scope, or marks as a follow-up. For each, ask: *"If we shipped the
   plan as written and stopped, would the feature meet the success criteria
   in `problem.md`?"* If no, the deferral is critical work, not follow-up work.
6. **Fourth pass — sequencing & rework.** Look at the order of steps. Flag any
   case where step N forces redoing step N-1, or where two steps would be
   cheaper merged, or where a step depends on something not yet built.
7. **Fifth pass — verification.** For each step, check the **Verify** clause.
   "Tests pass" is not verification. "`go test ./internal/foo -run TestBar`
   passes and the trace appears in Tempo" is verification. Vague verifies are
   findings.
8. **False-positive guard.** For each finding, try to invalidate it by re-reading
   the design or specs. If the design explicitly addresses your concern and you
   missed it, drop the finding. **If you cannot point to a concrete consequence
   of leaving the plan as-is, demote confidence or drop it.**
9. **Calibrate.** Ask: *"If I reviewed 10 plans of similar size with this prompt,
   how many would have a Critical?"* If your answer is more than 1–2, you are
   over-rating. Re-rank.

---

## Severity rubric

Severity = impact **if this plan executes as written**, given the stated blast
radius. Severity is not "how bad does this look" — it's "what breaks, ships
wrong, or has to be redone."

- **Critical** — Plan violates a design principle from the constitution
  (binary verdicts, two choke points, replay, verifiers don't act, OWASP
  coverage); omits a `problem.md` requirement with no acknowledgment; defers
  work the success criteria depend on; ships a tracer that crosses a
  security boundary without a verification step. Justifies blocking the plan.
- **High** — A tracer is actually a horizontal phase (one layer only) and
  nothing reaches the user until later; a deferral is described as
  follow-up but is load-bearing for the feature; sequencing forces non-trivial
  rework; a step touching production has no rollback.
- **Medium** — Tracer is end-to-end but split unnaturally (e.g., interface
  defined in tracer 1 but immediately changed in tracer 2); verification
  step is too vague to falsify; a step is too large to fit in one PR/session;
  REQ-IDs not referenced where they should be.
- **Low** — Out-of-scope section is missing or thin; rollback is named but
  underspecified for low-risk work; minor ordering nit that doesn't cause
  rework.
- **Info** — Frontmatter, doc location, naming, change log. No execution
  concern.

**Hard rule:** if you cannot name a concrete consequence of executing the plan
as written, the finding is **not** Critical and **not** High.

## Confidence rubric (orthogonal axis)

- **Confirmed** — The gap or violation is fully traceable to text in
  `problem.md` / `design.md` / `specs/` and the plan.
- **Likely** — Strong evidence in the docs; minor uncertainty about intent or
  about whether a deferral is acknowledged elsewhere.
- **Possible** — Plausible from the plan's shape, not fully verifiable without
  context outside these three docs.
- **Speculative** — Worth investigating; flagging an area, not claiming a defect.

A High-severity Speculative finding is a *question*, not a verdict. Mark it as such.

---

## Finding format

```
### [Severity:Confidence] Short title
- Location: plan.md §<section> / step N
- Category: design-compliance | tracer-integrity | deferral | sequencing | verification | scope | constitution
- What the plan says: brief quote or paraphrase of the relevant plan text
- What the design/problem requires: the specific obligation being missed or violated
- Consequence: what breaks, ships wrong, or has to be redone if executed as-is
- Fix sketch: minimum change to the plan that addresses the root cause
```

---

## Required output sections (in this order)

1. **Intent summary** — from step 1. Two paragraphs: what the feature must do, what the design proposes.
2. **Obligation checklist** — from step 2. Each obligation marked: covered (which step), partially covered, missing, or deferred.
3. **Tracer assessment** — one short paragraph per tracer: end-to-end? demoable? natural split? builds on prior tracer?
4. **Findings** — ordered by Severity then Confidence.
5. **Checked and clean** — areas you actively verified and have no concerns about (e.g., "rollback for step 4 is concrete and reversible"; "tracer 2 cleanly extends tracer 1's interfaces").
6. **Couldn't verify** — what you'd need to see for a complete review: missing specs, unclear blast radius, dependencies on other features. Be specific.
7. **Recommended fix order** — if findings depend on each other, the order to address them and why.

---

## Anti-patterns — do not do these

- Do not flag "consider adding more detail" without naming a specific step
  whose ambiguity would cause incorrect execution.
- Do not flag missing tests in the plan unless the design or specs require
  them and the plan omits them — implementation tests are an execution
  concern, not a plan concern.
- Do not propose alternative designs. The design is approved; review the plan
  against it. If you believe the design is wrong, that's a separate finding
  category and should be Speculative.
- Do not pad. A review with one Critical deferral and a clean rest-of-plan is
  more valuable than twelve Mediums and no signal.
- Do not mark anything Critical without naming the constitution principle,
  the missing requirement, or the failed success criterion.
- Do not paraphrase the plan back at me. Assume I can read it.
- Do not flag formatting, prose, or frontmatter unless required fields are
  missing or status is wrong.

---

## Example finding (format calibration)

### [Critical:Confirmed] Tracer 1 ships canary detection without replay fixture
- Location: plan.md §Steps / Step 3 ("Wire canary checker into Tool Gateway")
- Category: constitution
- What the plan says: Step 3 lands canary verdict emission and dashboard
  surfacing; replay fixture generation is listed under "Out of scope for this
  plan" with a note "follow-up tracer."
- What the design/problem requires: `design.md` §Approach states "every
  canary verdict produces a replay fixture at emission time"; `problem.md`
  success criterion #2 requires "any canary trigger is replayable from the
  trace alone." Constitution: replay is mandatory.
- Consequence: Tracer 1 ships a verdict-emitting path that violates the
  replay invariant. Either the tracer is incomplete (not demoable per the
  success criteria) or the invariant is silently weakened on main until
  the follow-up tracer lands.
- Fix sketch: Move replay-fixture write into Step 3, before the verdict is
  emitted. If that's too large, split Step 3 into 3a (verdict + fixture,
  no dashboard) and 3b (dashboard surfacing) — both still end-to-end.
