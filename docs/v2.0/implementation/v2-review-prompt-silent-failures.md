---
title: v2 Silent-Failure Review Prompt
type: review-prompt
status: ready
owner: brent
created: 2026-05-04
---

# v2 Silent-Failure Review Prompt

Paste verbatim as the first message in a fresh agent session. The reviewer
needs read access to the repo and the ability to run `git` / `rg` / `pytest`.

---

You're doing a focused review of jig (a multi-agent orchestrator) hunting for
silent failure patterns. Working directory: /Users/brent/Projects/personal/jig.

## Scope

The review target is every commit on `develop` ahead of `origin/develop` —
roughly 80 commits implementing the v2 design plus 4 review-remediation blocks.
Run `git log --oneline 7b3d7f6..HEAD` to enumerate. Baseline commit: 7b3d7f6.
HEAD is on develop.

This is a lot of code authored by Claude subagents in sequence; the operator's
concern is that defensive try/except patterns may have crept in across the
boundary. Per the operator's CLAUDE.md: "Diagnose root causes over adding
defensive try/except. No bare except, no swallowed exceptions. Fail loudly."

## What you're hunting

Find every place the code:

1. **Swallows an exception silently** — `except: pass`, `except Exception: pass`,
   `try: ... except: ...` with no log + no re-raise + no structured fallback
   that the caller can detect
2. **Returns None / empty / a sentinel on error** instead of raising or making
   the caller decide
3. **Retries without investigating root cause** — bare retry loops without
   exponential backoff or escalation, "try again and hope"
4. **Logs a warning and continues** when the design implies the operation must
   succeed (e.g., critical writes, gate checks, security-relevant paths)
5. **Catches too broadly** — `except Exception` when only specific exceptions
   should be expected
6. **Uses fallback values that hide errors** — `dict.get(key)` returning None
   silently when the key was supposed to be present, "or {}" defaults that mask
   missing data
7. **Has a try/except that wraps too much code** — catching errors from one
   line buried inside a 50-line block, where only one specific call should be
   guarded

Some try/except blocks are legitimate. Don't flag:
- Best-effort cleanup in `finally` (legitimate; cleanup must not propagate)
- Best-effort analytics emission (the design explicitly says analytics
  capture must not bring down the orchestrator)
- I/O-error retries with bounded retry policy + clear escalation path
- Catching specific exceptions to handle a known failure mode

The boundary: legitimate patterns log + degrade gracefully + emit a structured
error the operator can see. Silent failures swallow + continue + the operator
never knows.

## High-suspicion areas (start here)

These are the integration hotspots where silent failure is most likely:
- `jig/orchestrator.py` — agent lifecycle, dev provisioning, federation gate
- `jig/agent.py` — Claude SDK integration, tool call handling
- `jig/store/*.py` — JSONL stores, especially the analytics emitter
- `jig/dev_env/*.py` — provisioning, fixtures, sweepers, orphan tracking
- `jig/reviewers/*.py` — federation dispatch, comment posting, auto-apply
- `jig/sim/driver.py` — scenario step dispatch
- `jig/hooks/per_commit_runner.py` — git-hook integration

## How to grep

Useful patterns:

```bash
rg "except\s+\w*:\s*$" jig/                # bare except
rg "except.*:\s*pass" jig/                  # except: pass
rg "except.*:\s*continue" jig/              # except: continue
rg "except.*:\s*return None" jig/           # silent None return on error
rg "except.*Exception" jig/                 # broad catches
rg "_logger.warning.*exc_info" jig/         # log + continue
rg "\.get\(" jig/ | grep -v "def get"      # silent dict gets (high false positive)
rg "asyncio.create_task" jig/               # fire-and-forget tasks
```

But don't just read grep output; trace context. A `_logger.warning` is fine if
the caller can tell something failed (returns None vs raises a specific
exception). It's silent failure if the caller cannot.

## Output format

Markdown, three sections:

### Critical silent failures
Patterns that hide actual bugs from the operator: would-be-shipped errors that
nothing surfaces. Each finding: file:line, the exception path, what the caller
sees, why it's silent, suggested fix.

### Important silent failures
Patterns that violate the spirit of "fail loudly" but where the impact is
diagnosable post-hoc (e.g., it's logged but operator has to know to look).
Each finding: file:line, the pattern, suggested change.

### Notable observations
Defensive patterns that aren't broken but are suspicious — large try/except
blocks, redundant guards, places where the real fix would be upstream
validation.

## What NOT to focus on

- Style nitpicks
- Test coverage
- Documentation prose quality
- The legitimate analytics-don't-propagate-failures pattern (it's design)
- Cleanup-in-finally patterns (legitimate)
- Best-effort retries with bounded retry + escalation (legitimate)

## Discipline

If you find yourself wanting to refactor or design something new — write it
as a v2.x note instead. The review is checking what landed.

End with one sentence: "fail-loudly discipline is good / fail-loudly
discipline is broken in N places" + the two-or-three biggest offenders.
