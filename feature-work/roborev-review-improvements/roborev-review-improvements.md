# Review Quality Improvements from roborev Analysis

Jig's review system is already well-designed. This doc identifies the specific gaps and provides exact text to add.

---

## What Jig already does well (no changes needed)

- Multi-reviewer with scoped specialization
- Evidence requirement ("if you cannot construct one, demote or drop")
- Harm-based severity definitions
- Explicit "do not report" lists per reviewer
- Confidence scoring with numeric calibration
- Hard rule: "cannot name a concrete failure scenario → not Critical/High"
- Explicit clean-pass permission ("emit zero comments when the diff is correct")
- Adversarial second pass in code-reviewer.md

---

## Gap 1: Pre-finalization consistency check (highest value)

**What roborev does:** Before outputting, the reviewer runs a structured self-audit:
> "Every finding must reference the narrowest applicable location, the severity must match the impact you described, and no two findings should contradict each other. Drop any finding that fails these checks."

**What Jig has:** Step 5 (false-positive guard) asks "can I construct a trigger?" and step 6 (calibrate) asks about over-rating. These are good but catch different failure modes. The missing piece is a **coherence audit** — checking that location, severity, and impact are internally consistent *across* the finding's own fields.

**The failure mode it prevents:** A finding where the trigger is vague, the location is file-level when a line is known, or the severity is High but the Impact sentence only describes a minor inconvenience. These pass the "can I construct a trigger" check but are still noise.

### Change: `prompts/code-reviewer.md` step 6

Replace the current calibrate step:

```
6. **Calibrate.** Before writing the final report, ask: *"If I reviewed 10 similar files with this prompt, how many would have a Critical?"* If your answer is more than 1–2, you are over-rating. Re-rank.
```

With:

```
6. **Calibrate and drop.** Two checks before writing the final report:

   a. *Severity calibration:* Ask "If I reviewed 10 similar files with this prompt, how many would have a Critical?" If your answer is more than 1–2, you are over-rating. Re-rank.

   b. *Coherence audit:* For each finding, verify all three:
      - **Location** is the narrowest you can give (line number when possible; file-level only when the issue is an omission or spans the whole file).
      - **Severity** matches the Impact you wrote. If your Impact says "minor inconvenience" but severity says High, one of them is wrong — fix it or drop the finding.
      - **No two findings contradict each other** (e.g., flagging both "this lock is unnecessary" and "this code is missing a lock").

   Drop any finding that fails the coherence audit. Do not soften it to Notable — drop it.
```

---

## Gap 2: Toolchain/dependency grounding

**What roborev does:** Explicitly tells reviewers not to rely on model memory for whether an API or feature exists:
> "Judge whether a feature or API exists from the project's toolchain and dependency manifests, not your own memory, which may be stale. This cuts both ways: do not flag valid recent features as broken, and do not miss calls to APIs that genuinely do not exist for the project's versions."

**What Jig has:** Nothing. Reviewers can flag an API as "this doesn't exist" based on stale training data, or miss a call to a genuinely absent API.

**The failure mode it prevents:** Python moves fast. `asyncio` API changes, Pydantic V1→V2 differences, new stdlib additions — a reviewer trained on old data will produce false positives and false negatives on these.

### Change: Add to `prompts/code-reviewer.md` anti-patterns section

Add after the last anti-pattern bullet:

```
- Do not flag an API, method, or language feature as nonexistent based on your own memory. Check `pyproject.toml`, `uv.lock`, `requirements.txt`, or the relevant manifest first. Model memory goes stale; the manifest is authoritative. This cuts both ways — do not flag valid recent additions as broken, and do not miss calls to APIs that genuinely do not exist for the pinned versions.
```

### Change: Add to reviewer role YAML files

Add to the **Mechanics** section of `reviewer_generalist.yaml`, `reviewer_security.yaml`, `reviewer_error_handling.yaml`, and `reviewer_performance.yaml`:

```yaml
  - Before flagging an API or stdlib feature as nonexistent or unavailable,
    read ``pyproject.toml`` (or the relevant manifest) to confirm the pinned
    version. Do not rely on memory; it goes stale.
```

(The architectural and pattern-conformance reviewers rarely flag missing APIs, so lower priority for those two.)

---

## Gap 3: "No process narration" in structured output

**What roborev does:** Explicitly prohibits narrating intermediate work in the final output:
> "Return only the final review. Do NOT narrate your process, mention files you opened, or describe intermediate checks. If you use tools while reviewing, finish all tool use before emitting the final review."

**What Jig has:** The role YAML files instruct reviewers to call `reviewer_post_comment` for findings. But `code-reviewer.md` (used for direct invocation or embedded contexts) doesn't prohibit process narration.

**The failure mode it prevents:** Reviews that read "Step 1: I'm looking at the intent... Step 2: I'm now looking for invariants..." instead of just findings.

### Change: Add to `prompts/code-reviewer.md` required output section

Add before the numbered list:

```
**Format discipline:** Return only the final report in the sections below. Do not narrate intermediate steps, mention files you opened, or describe what you checked and found clean mid-process. If you read additional files during review, finish reading before writing the report. All tool use before the final output; the report after the last tool call.
```

---

## Gap 4: Explicit "Impact" requirement on all findings (minor tightening)

Jig's code-reviewer.md finding format already has an `Impact:` field, which is good. The one thing roborev adds is requiring the impact to be stated as **concrete harm**, not abstract principle:

> "What specifically goes wrong if this is not fixed (concrete harm, not 'violates best practices')"

Jig's calibrate step and severity rubric imply this, but it's not stated in the finding format itself.

### Change: `prompts/code-reviewer.md` finding format

Update the Impact line from:
```
- Impact: what breaks, given the stated blast radius
```

To:
```
- Impact: the concrete harm if this ships unfixed — name what breaks, who is affected, under what conditions. "Violates best practices" is not an impact.
```

---

## Priority order

1. **Gap 1 (coherence audit in calibrate step)** — highest signal-to-noise ratio improvement. Addresses the case where the reviewer's logic is internally inconsistent. Applies to `code-reviewer.md` directly.

2. **Gap 2 (toolchain grounding)** — prevents false positives on Python API questions, which are common. One addition to `code-reviewer.md` and four role YAML files.

3. **Gap 4 (Impact field tightening)** — small wording change, but closes the "violates best practices" escape hatch.

4. **Gap 3 (no process narration)** — low priority because role-based reviewers use structured tool calls, so narration is less of a problem. More relevant if `code-reviewer.md` gets used in embedded contexts.

---

## What roborev does that Jig intentionally doesn't need

- **Previous review context injection** — roborev feeds nearby commit reviews to prevent re-raising resolved issues. Jig's ticket model handles this differently: the reviewer has access to `ticket://description` which includes prior decisions. Different architecture, same goal.
- **Intent-implementation gap** — roborev checks "does the diff match the commit message?" Jig's spec-compliance mechanical reviewer does this more rigorously against structured AC.
- **Multi-agent synthesis** — Jig already does this via lead-reviewer agent with semantic dedup.
