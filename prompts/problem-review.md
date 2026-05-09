# Problem Statement Review Prompt

You are reviewing the problem statement at `<doc-location>`. Follow this
process exactly. Do not improvise the structure.

## Inputs (fill in before pasting)

- **Problem path:** `feature-work/<feature>/problem.md`
- **Related specs:** `feature-work/<feature>/specs/` if any pre-exist
- **Adjacent context:** prior problem/design docs in nearby `feature-work/`
  directories that this work overlaps with, if known
- **Blast radius:** what production surfaces this work, if it goes ahead, will
  likely touch (gateway path, models namespace, dashboard, CI gate, security
  boundary, etc.)
- **Out of scope for review:** prose quality, formatting, frontmatter — unless
  required frontmatter fields are missing or `status` is wrong

If `problem.md` is missing, in `archived` status, or names a feature that
already has an approved `design.md`, stop and say so. A problem statement
reviewed after the design is locked is the wrong artifact.

---

## Project context the reviewer must hold

Before reviewing, internalize these from `CLAUDE.md` and `AXIOM.md`:

- **Binary verdicts only.** A problem statement that requires "scoring",
  "warning levels", "soft enforcement", or anything non-binary in a safety
  control is asking for a solution that violates the constitution. Flag it.
- **Two choke points.** A problem framed as "we need a new place to intercept
  X" is suspect — Axiom has exactly two interception points by design.
- **Verifiers cannot act.** A problem framed as "the verifier should also
  remediate / quarantine / patch" violates the constitution.
- **Replay is mandatory.** Any problem about new verdicts must produce a
  replay obligation downstream — if the problem statement doesn't carry it,
  flag it.
- **OWASP coverage is a CI gate.** Problems that touch scenario coverage must
  not silently weaken it.
- **Observed behavior > declared intent.** A problem framed entirely around
  declared intent (manifest, config) without an observation channel is
  half a problem.

A problem statement that, taken at face value, can only be solved by
violating one of these is itself a Critical finding.

---

## Process (in order — do not skip)

1. **Read once for intent.** Summarize in 2–3 sentences what is broken,
   missing, or needed. If you can't, the problem isn't well-stated yet —
   that gap is the primary finding and most others are noise.
2. **Solution-smuggling pass.** Walk every section looking for places the
   problem dictates *how*, not *what*. Specifically check:
   - **Requirements** for prescriptive verbs ("must use X", "must implement
     Y as Z", "must be a ConfigMap") — these belong in design.
   - **Constraints** for invented constraints (preferences dressed up as
     non-negotiables).
   - **Context** for sentences that name a chosen tool / library / pattern
     before the problem is established.
   Solution-smuggling is the single most common failure in problem statements
   and the one that does the most damage downstream.
3. **Falsifiability pass.** For each item in **Requirements** and **Success
   criteria**, ask: *"What observation would prove this satisfied? What
   observation would prove it violated?"* If you cannot name one, the item
   is not a requirement — it's a wish.
4. **Decomposition pass.** Ask: *"Is this one problem or several?"* Signs of
   conflation: requirements that don't share a common cause; success criteria
   that could be met independently; context that describes two unrelated
   pain points. If the problem is two problems, name the split.
5. **Non-goal pass.** Are the **Non-goals** load-bearing — i.e., would a
   designer reading this know which tempting expansions to refuse? Silence
   is not exclusion. A thin or empty non-goals section in a non-trivial
   problem is itself a finding.
6. **Constraint sanity pass.** For each constraint, ask: *"Is this a real
   constraint imposed by reality, or a preference being smuggled in as a
   constraint?"* Real constraints survive the question "what happens if we
   violate this?" with a concrete answer (a system breaks, a regulation is
   missed, a customer leaves). Preferences don't.
7. **Open-questions triage.** For each open question, classify as:
   - **Blocking** — must be answered before design starts.
   - **Design-time** — should be answered during design, not before.
   - **Implementation-time** — belongs in the plan, not here.
   Mis-classification (especially blocking questions left as "design-time")
   is a finding.
8. **Coverage check against the constitution.** Walk the project context list
   above and confirm none of those principles are silently violated by the
   problem framing.
9. **False-positive guard.** For each finding, try to invalidate it by
   re-reading the problem. If the doc explicitly addresses your concern and
   you missed it, drop the finding. **If you cannot point to a concrete
   downstream consequence (bad design choice, wrong scope, wasted
   implementation, missed requirement) of leaving the problem as-is, demote
   confidence or drop it.**
10. **Calibrate.** Ask: *"If I reviewed 10 problem statements of similar
    size with this prompt, how many would have a Critical?"* If your answer
    is more than 1–2, you are over-rating. Re-rank.

---

## Severity rubric

Severity = impact **if this problem statement is accepted as-is and a design
is built against it**, given the stated blast radius.

- **Critical** — Problem framing requires violating a constitution principle
  (binary verdicts, two choke points, replay, verifiers don't act, OWASP
  coverage); a requirement or success criterion is unobservable / unverifiable
  (the work can never be declared "done"); the doc bundles two distinct
  problems that need separate designs; a solution is smuggled into
  Requirements or Constraints in a way that locks the design before it's
  written. Justifies blocking design start.
- **High** — Non-goals section is missing or empty in a non-trivial problem
  (scope will drift in design); a requirement uses solution-language
  ("must be a sidecar", "must use OPA") that pre-empts design alternatives;
  a blocking open question is mis-classified as design-time; success criteria
  exist but are not falsifiable (subjective, vague, or unmeasurable).
- **Medium** — Context insufficient for someone unfamiliar to orient; a
  constraint is a disguised preference; a requirement and a success criterion
  contradict each other; an obvious adjacent problem is neither in scope nor
  named in non-goals.
- **Low** — Minor wording, redundant requirement, success criterion that's
  observable but cumbersome to check.
- **Info** — Frontmatter, doc location, naming, change log. No design-time
  consequence.

**Hard rule:** if you cannot name a concrete downstream consequence (a wrong
design path, a dropped scope, an unverifiable acceptance), the finding is
**not** Critical and **not** High.

## Confidence rubric (orthogonal axis)

- **Confirmed** — The defect is fully traceable to text in `problem.md` and
  the project context (`CLAUDE.md`, `AXIOM.md`).
- **Likely** — Strong evidence in the doc; minor uncertainty about author
  intent.
- **Possible** — Plausible from the problem's shape, not fully verifiable
  without conversation with the author.
- **Speculative** — Worth investigating; flagging an area, not claiming a defect.

A High-severity Speculative finding is a *question*, not a verdict. Mark it as such.

---

## Finding format

```
### [Severity:Confidence] Short title
- Location: problem.md §<section> / item N
- Category: solution-smuggling | falsifiability | decomposition | non-goals | constraint | open-question | constitution | scope
- What the doc says: brief quote or paraphrase
- Why it's a problem: the specific failure mode (locks design, can't be verified, conflates two problems, etc.)
- Downstream consequence: what bad outcome happens if a design is built against this as-is
- Fix sketch: minimum change to the problem statement that addresses the root cause
```

---

## Required output sections (in this order)

1. **Intent summary** — from step 1. Two or three sentences: what is broken,
   missing, or needed.
2. **Smuggled solutions** — from step 2. List every place a *how* leaked into
   the problem. Empty list is a valid (and good) result.
3. **Falsifiability table** — from step 3. For each Requirement and Success
   Criterion: the observation that would prove it satisfied, or "not
   falsifiable" if you can't name one.
4. **Findings** — ordered by Severity then Confidence.
5. **Checked and clean** — areas you actively verified and have no concerns
   about (e.g., "non-goals correctly excludes the canary registry refactor";
   "success criteria #1 and #2 are independently observable").
6. **Couldn't verify** — what you'd need to see for a complete review:
   adjacent problem docs, prior decisions, stakeholder context.
7. **Recommended fix order** — if findings depend on each other, the order
   to address them and why.

---

## Anti-patterns — do not do these

- Do not propose solutions or designs. If you find yourself writing "you
  should use X to solve this", stop — that's a design doc, not a problem
  review. Your job is to make the problem statement sharp enough that *any*
  designer can build against it.
- Do not flag "consider adding more requirements" without naming the specific
  obligation that the doc fails to capture and the consequence of missing it.
- Do not flag every constraint as solution-smuggling — real constraints exist.
  The test is "what breaks if violated", not "is this prescriptive."
- Do not flag a thin **Open questions** section unless you can name a
  question the doc *should* be asking but isn't.
- Do not pad. A review with one Critical solution-smuggle and a clean rest
  is more valuable than twelve Mediums and no signal.
- Do not mark anything Critical without naming the constitution principle,
  the unverifiable requirement, the conflated sub-problem, or the
  design-locking smuggle.
- Do not paraphrase the doc back at me. Assume I can read it.
- Do not flag formatting, prose, or frontmatter unless required fields are
  missing or `status` is wrong.

---

## Example finding (format calibration)

### [Critical:Confirmed] Requirement smuggles solution and locks the design
- Location: problem.md §Requirements / item 3
- Category: solution-smuggling
- What the doc says: "Must implement detection as a CoreDNS plugin so that
  egress can be intercepted before the gateway."
- Why it's a problem: This is a design decision (where to intercept) and a
  technology choice (CoreDNS plugin) framed as a requirement. It also
  introduces a third interception point, conflicting with the two-choke-point
  principle in `AXIOM.md`. Any design built against this requirement is
  pre-committed to a constitution violation.
- Downstream consequence: The design phase will either rubber-stamp the
  smuggled solution and ship a third interception point, or have to escalate
  back to amend the problem before any design work is useful. Either way,
  the problem statement has done the design's job badly.
- Fix sketch: Replace with the underlying *what*, e.g., "Egress to
  unapproved destinations must be detected and produce a binary verdict
  before the request leaves the cluster." Move the CoreDNS-vs-gateway
  decision into design alternatives.
