## Outcome

All 8 tickets resolved in 55 min 30 s at $8.46. The linear T1→T2→T3→T4 feature chain completed without stalls or merge failures. T1 (Core) required three review→implement cycles; T2 and T3 cleared review in one pass each. The single operator question was a legitimate plan-approval gate.

---

## Trajectory

- `14:20:17` — Run started; brief auto-approved (`--brief`)
- `14:22:00` — Spec published; spec-generator emitted advisory: `--type ask/show` semantics mismatched against HN Firebase API `type` field (both are `type: story`, distinguished by title prefix)
- `14:22:36` — Profile selected: `small`
- `14:24:11` — SA proposed `python` scaffold with typer + httpx + asyncio; `--type` advisory not resolved in arch notes
- `14:25:42` — PM posted plan, asked operator to approve
- `14:26:04` — Operator replied `Y`; PM re-ran to create tickets ($0.25 + $0.32 for two PM runs)
- `14:27:00` — T1 (Core) started; test agent emitted 5 test files across 19 turns ($0.92)
- `14:34:36` — Dev implemented T1 in 47 turns ($1.03); 48/48 tests pass
- `14:39:06` — Review 1: blocked on RC-1 (`Story.url` hard-indexing KeyError) + RC-2 (`asyncio.gather` without `return_exceptions=True`)
- `14:42:02` — Dev fixed RC-1/RC-2 in 20 turns ($0.40)
- `14:43:43` — Review 2: blocked on RC-4 (None url renders as literal `"None"` in formatter output)
- `14:45:59` — Dev fixed RC-4 in 12 turns ($0.25)
- `14:46:49` — Review 3: passed; RC-3 (asyncio exception propagation nit) flagged advisory only
- `14:50:17` — T1 validated (48/48, ruff, mypy --strict clean)
- `14:50:19` — T2 (Filtering) started; passed review in one pass; validated at `15:01:44`
- `15:01:46` — T3 (JSON output) started; dev implemented in 24 turns ($0.52)
- `15:05:46` — T3 review started: **38 turns, 417 s, $0.47**; reviewer navigated worktree across multiple Glob/Read cycles before settling on diff
- `15:12:43` — T3 review passed; validated at `15:13:43`
- `15:13:46` — T4 (Integration validation) ran as final validate-only ticket; passed at `15:15:47`

---

## Operator-question quality

**One question asked:**

> "Plan posted above (4 tickets, linear chain T1→T2→T3→T4). Approve to create tickets, or request changes?"

**Verdict: `product/scope`** — plan approval requires operator judgment on scope, ordering, and ticket granularity. Correct to ask. No tool could have answered this.

---

## Friction (efficiency)

**T1 review oscillation — 3 review→implement cycles, 71 reviewer turns, $0.98 reviewer cost:**

All three findings were real (url optionality, asyncio exception handling, None rendering). However, RC-1 and RC-2 were predictable from HN API characteristics: the spec-generator already flagged that Ask HN / Show HN posts behave differently, and the Firebase API docs are well-known to omit `url` on non-story items. Neither the SA's architecture notes nor the test agent's fixtures covered items without `url` fields. If SA had encoded `Story.url: Optional[str]` in the design doc, dev would have implemented it correctly on the first pass, eliminating both Review 1 (31 turns, $0.43) and Review 2 (26 turns, $0.32) — saving ~$0.75 in reviewer cost and ~$0.65 in dev cost, plus ~12 minutes wall time.

**T3 reviewer navigation — 38 turns, 417 s, $0.47 for a 2-function change:**

The implementation added one function to `formatter.py` and one flag to `__main__.py`. The reviewer used at least 4 exploratory tool calls to locate source files ("Let me explore the worktree structure", "Let me look at all source files", "Let me look directly within the worktree") before settling on a Read of the diff. `test_cli.py` was read at least twice from the same tool-result pattern. A single `Glob("src/**/*.py")` at turn 1 would have given the complete file list. The comparable T1 first-pass reviewer (31 turns) completed in 175 s; T3's reviewer took 2.4× longer despite reviewing a smaller change.

**PM double-run — $0.57 total, 176 s:**

The first PM run ended in `needs_info` after posting the plan ($0.25, 70 s). After the operator replied `Y`, the PM re-ran from scratch ($0.32, 106 s), re-issuing `ToolSearch` for `create_ticket` / `update_ticket` and re-reading spec/arch context it had already loaded. This is pure overhead — the second run's only meaningful work was the four `create_ticket` calls.

**test:a72dd44b — tokens_in=1020 (vs. 24 and 15 for T1/T3 test agents):**

The filtering test agent read extensively across existing test files and source to understand fixture patterns before writing `test_filtering.py`. Not a problem per se, but duplicating `_run_cli` and `LINE_PATTERN` from `test_cli.py` rather than using `conftest.py` was a missed opportunity the reviewer later flagged.

**Individual run costs above $0.50:**
- `test:9b16a010` — $0.92 (19 turns, 5 test files)
- `dev:9b16a010` — $1.03 (47 turns, full implementation)
- `pm:planning` retry — $0.32 (9 turns, just ticket creation)

---

## Brief fidelity

**`brief.md` was empty in this input.** Fidelity assessment is based on spec-generator structured output, ticket titles, and thread evidence.

Inferred capabilities from spec-generator:

| Capability | Status |
|---|---|
| Fetch top stories (`hn top --limit N`, default 10) | ✓ implemented — T1, 48 tests, CLI validated |
| Filter by minimum score (`--min-score N`) | ✓ implemented — T2, `filters.py`, 30 tests |
| Filter by type (`--type ask\|show`) | ✓ implemented (tests pass) — but **see below** |
| JSON output (`--format json`) | ✓ implemented — T3, `format_stories_json`, 35 tests |
| Integration validation | ✓ implemented — T4, 113/113 tests pass |
| Caching | ✓ deferred (rationale: `planned_uncommitted` per spec-generator) |
| Pagination | ✓ deferred (rationale: `planned_uncommitted` per spec-generator) |
| Realtime / TUI / auth | ✓ non-goals, correctly excluded |

**Highest-priority finding — `--type ask/show` semantics:**

The spec-generator explicitly flagged: *"The HN Firebase API item type field does not include distinct 'ask' or 'show' values for Ask HN / Show HN posts — both are typically type: story distinguished by title prefix."* SA did not record a resolution in arch notes. The test agent created fixtures with `type: "ask"` and `type: "show"` as if these were real API values. The filtering implementation matched against these fixture values and all tests pass — but against the live HN Firebase API, `--type ask` and `--type show` would silently return zero results because no live item carries those type values. This is a **correctness defect that escaped all review phases**. It is not a silently-dropped requirement (the flag was built) but it is silently-broken against live data.

---

## Right-sizing the build

**Appropriate:**
- `src/hn_cli/filters.py` as a separate module for filtering logic — clean, testable separation
- `asyncio.gather` for parallel item fetches — correct for I/O-bound HN API calls
- `HN_FIXTURE_PATH` eval mode in `client.py` — enables deterministic integration tests without live HTTP

**Under-engineered:**
- `tests/conftest.py` is underused. `_run_cli()` (subprocess helper) and `LINE_PATTERN` (regex) were copy-pasted into both `test_cli.py` and `test_filtering.py`. The reviewer flagged this (finding in `reviewer-generalist:a72dd44b` review summary) but it wasn't fixed. With a third test file (`test_json_output.py`) also added, conftest.py should carry the shared subprocess runner.
- `--type ask/show` implemented as a direct string match against the `type` field with no title-prefix fallback. Given the spec-generator advisory, SA should have specified the implementation strategy; the ambiguity was never resolved in code, only in test fixtures.

**No over-engineering observed.** Build complexity is appropriate for a small CLI.

---

## Tool-use anti-patterns

1. **reviewer-generalist on T3 (ticket `7b8f92dc`)**: Four exploratory navigation calls before reading the diff — "Let me explore the worktree structure", "Let me look at all the source files in the project", "Let me look directly within the worktree for the dev's changed files". `Glob("src/**/*.py")` at turn 1 would have returned the full source list in one call. Duration: 417 s, 38 turns vs. dev's 119 s, 24 turns for the same ticket.

2. **test:a72dd44b `tokens_in=1020`** vs. `test:9b16a010` at 24 and `test:7b8f92dc` at 15: The filtering test agent read all prior test files to understand patterns, then duplicated helpers rather than extending `conftest.py`. The reads were not wasteful individually, but the output produced duplication.

3. **PM re-runs ToolSearch after needs_info resolution**: Both PM runs issue `select:mcp__jig__spec_list_capabilities,...` and `select:mcp__jig__create_ticket,...` ToolSearch calls. The second run's useful payload is just four `create_ticket` calls; the context-loading adds ~1 minute and ~$0.10.

---

## Quality observations

**Dependency graph**: Correctly linear — each ticket genuinely depends on the prior implementation. No parallelism opportunity was missed.

**Review quality**: Reviewer was substantive on T1. RC-1, RC-2, RC-4 are all real bugs the reviewer correctly blocked on. Review on T2 and T3 was lighter but appropriate given the smaller diffs. The T3 reviewer's 38-turn run was navigation overhead, not depth — the final findings were reasonable.

**Test proportionality**: 48 tests for the core (models, client, formatter, CLI), 30 for filtering, 35 for JSON output. Proportional to risk. The integration test approach (subprocess via `HN_FIXTURE_PATH`) is correct for a CLI.

**Ruff warning on test commit**: `test:9b16a010` first commit attempt raised "ruff check found 17 unfixable issues" and had to retry. This consumed extra time and suggests the test agent wrote test files with lint violations that couldn't be auto-fixed (likely unused imports or bare assertions in fixture files). The second commit succeeded but the underlying test code quality should have been cleaner on the first pass.

---

## Recommendations

1. **[blocking] SA prompt (`jig/defaults/roles/sa.yaml` or equivalent)**: Add explicit instruction: *"If the spec-generator emitted any advisory notes, record a resolution for each in the architecture doc before marking the architecture ticket resolved. If resolution requires a design choice (e.g., API field mapping), record the chosen approach."* — Would have caught the `--type ask/show` semantics gap and prevented a correctness defect from shipping through all phases undetected.

2. **[blocking] reviewer-generalist prompt**: Add navigation instruction at the top: *"Before reading any source file, run `Glob('src/**/*.py')` and `Bash('git diff HEAD~1 --name-only')` to enumerate changed files. Do not explore directory structure incrementally."* — Would reduce T3-style navigation inflation from ~38 turns to ~20 and cut review time by half on small diffs.

3. **[improvement] PM prompt — `needs_info` resumption path**: When the PM resumes after `needs_info` with a simple approval, it should skip spec/arch context re-loading and jump directly to `create_ticket` calls. Add a conditional in the PM prompt: *"If your previous run already drafted a plan and the operator has approved it (you will see their reply in the thread), skip all context-loading steps and proceed directly to creating the tickets from the plan you already drafted."*

4. **[improvement] SA architecture notes template**: Add a field for `api_field_optionality` or `api_quirks` where SA records known external API behaviors (e.g., "HN `/item` endpoint omits `url` on Ask HN, job, and poll items; model field must be `Optional[str]`"). Dev and test agents should be instructed to check this field before writing models or fixtures.

5. **[improvement] test agent prompt**: Add: *"If subprocess helper functions (`_run_cli`, `_run_command`) or regex patterns are already present in another test file, add them to `conftest.py` instead of duplicating them. Check `tests/conftest.py` before writing any helper."* — Addresses the `test_filtering.py` / `test_cli.py` duplication the reviewer flagged but that was never fixed.

6. **[nit] test agent prompt**: Add: *"Before committing, run `ruff check --select ALL` and fix all fixable issues. For genuinely unfixable issues in test files (e.g., `PT` rules on fixture functions), add `# noqa` with a reason rather than leaving bare violations."* — Eliminates the retry commit caused by 17 unfixable ruff issues on `test:9b16a010`.
