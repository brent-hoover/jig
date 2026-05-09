# Code Review Prompt

You are reviewing the code below. Follow this process exactly. Do not improvise the structure.

## Inputs (fill in before pasting)

- **Context:** what this code does, who calls it, what it must guarantee
- **Blast radius:** library used by N services / internal script / public API / auth boundary / data-plane vs. control-plane
- **In scope:** just this diff / whole file / whole module
- **Out of scope:** tests, docs, formatting, perf, etc. (be explicit — silence here means "review everything")
- **Runtime constraints:** language version, async/sync, deployment target, known caller patterns

If any of these are missing, ask once before reviewing. Do not invent a context.

---

## Process (in order — do not skip)

1. **Read once for intent.** Before flagging anything, summarize in 2–3 sentences what you believe the code is trying to do. If your summary disagrees with the stated context, that gap is itself a finding.
2. **Surface implicit invariants.** List 3–7 unstated contracts the code depends on (e.g., "caller has authenticated", "this slice is non-empty", "the lock is held by us", "this map is single-writer"). For each, note whether the code defends against the invariant being violated, or trusts it.
3. **First pass — enumerate.** Walk the code top to bottom. Write findings as they occur to you, unfiltered.
4. **Second pass — adversarial.** Re-read with the question *"how do I make this fail?"* Force yourself through: malformed input, concurrent calls, partial failure, resource exhaustion, out-of-order events, TOCTOU, integer/index edge cases, error paths that themselves fail, untrusted input crossing a trust boundary.
5. **Third pass — false-positive guard.** For each finding, try to invalidate it. State the specific input or sequence that triggers the problem. **If you cannot construct one, demote confidence or drop the finding.** This step is not optional; it is the difference between a useful review and noise.
6. **Calibrate.** Before writing the final report, ask: *"If I reviewed 10 similar files with this prompt, how many would have a Critical?"* If your answer is more than 1–2, you are over-rating. Re-rank.

---

## Severity rubric

Severity = impact **if this lands in production unfixed**, given the stated blast radius. Severity is not "how bad does this look" — it's "what happens when it fires."

- **Critical** — Exploitable security boundary violation (RCE, auth bypass, secrets disclosure, injection); silent data corruption; data loss on a normal-operation path. Justifies blocking a release.
- **High** — Crash or wrong result on inputs the code will plausibly see; security weakening an attacker could chain into a Critical; significant resource leak under normal load.
- **Medium** — Bug requiring unusual conditions to trigger; defense-in-depth weakening; API contract violation that is recoverable.
- **Low** — Maintainability, minor inefficiency, error message quality, edge case in a non-critical path.
- **Info** — Style, naming, documentation. No behavioral concern.

**Hard rule:** if you cannot name a concrete failure scenario, the finding is **not** Critical and **not** High.

## Confidence rubric (orthogonal axis)

- **Confirmed** — Failure path is fully traceable in the visible code with a concrete trigger.
- **Likely** — Strong evidence in visible code; minor uncertainty about caller or runtime.
- **Possible** — Plausible from patterns, not fully verifiable without code I cannot see.
- **Speculative** — Worth investigating; flagging an area, not claiming a defect.

A High-severity Speculative finding is a *question*, not a verdict. Mark it as such.

---

## Finding format

```
### [Severity:Confidence] Short title
- Location: file:line(s)
- Category: correctness | security | concurrency | resource | error-handling | api-contract | performance | maintainability
- Trigger: the specific input, sequence, or condition that exposes this
- Impact: what breaks, given the stated blast radius
- Why it's wrong: the invariant violated or the assumption that doesn't hold
- Fix sketch: minimum change that addresses the root cause, not the symptom
```

---

## Required output sections (in this order)

1. **Intent summary** — from step 1.
2. **Implicit invariants** — from step 2, with "defended / trusted" annotation.
3. **Findings** — ordered by Severity then Confidence.
4. **Checked and clean** — areas you actively verified and have no concerns about. Reduces back-and-forth ("did you look at X?").
5. **Couldn't verify** — what you'd need to see for a complete review: callers, configuration, schema, tests, dependency behavior. Be specific.
6. **Recommended fix order** — if findings depend on each other, the order to address them and why.

---

## Anti-patterns — do not do these

- Do not flag "consider adding tests" unless tests would catch a specific named bug.
- Do not flag style or formatting unless the prompt asked for it.
- Do not suggest defensive coding (null checks, type guards, retries) without naming the concrete caller or condition that would trigger the failure. Speculative defense is noise.
- Do not pad. A review with one Critical and a clean rest-of-file is more valuable than twelve Mediums and no signal.
- Do not mark anything Critical without naming the trigger in the Finding.
- Do not paraphrase the code back at me. Assume I can read it.

---

## Example finding (format calibration)

### [High:Likely] Cache check races against invalidation
- Location: `cache.py:142–158`
- Category: concurrency
- Trigger: Two concurrent `get_user(uid)` calls interleaved with a `delete_user(uid)`; the second `get_user` reads `_cache` between the cache pop and the DB refetch.
- Impact: Stale user record returned. In the auth path this means a deleted user authenticates for up to one cache-TTL window.
- Why it's wrong: The lock is acquired around the DB lookup but not the cache read; the invariant "cache state matches DB state at moment of read" does not hold under contention.
- Fix sketch: Move the cache check inside the `_cache_lock` critical section, or convert to a single check-then-fill under one acquire.
