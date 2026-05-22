---
title: Test Rigor — Design
type: design
status: active
owner: brent
created: 2026-05-22
updated: 2026-05-22
problem: ./problem.md
---

# Test Rigor — Design

## Summary

Ship a default check catalog at `jig/defaults/checks.yaml`, fall back to it from `load_check_catalog`
when the project doesn't override, and declare scripted `automated_checks` on the `test`,
`implement`, and `validate` phases of every shipped workflow. Phase-level checks (`test`,
`implement`) scope to the current ticket's diff via a `python -m jig.check_helpers.pytest_diff`
helper so they stay fast at scale. The end-of-ticket `validate` phase runs the full suite + lint +
mypy.

The handoff gate already bounces failing handoffs back to the agent — no orchestrator changes
needed. The work is mostly catalog authoring + a small diff-parsing helper + one fallback path in
`load_check_catalog`.

A separate, layered PR adds `reviewer-test-quality` for the judgment concerns (test code quality,
fixture correctness vs spec-generator advisories, pytest idiom conformance). That's out of scope
for this design.

## Approach

### Shipping checks as defaults

`load_check_catalog` (`jig/checks.py:82`) currently returns an empty catalog when
`.jig/checks.yaml` is absent. The change: fall back to `jig/defaults/checks.yaml` (shipped) when
the project-local file is missing or empty, mirroring the loader pattern in
`jig.persistence.load_role` and `jig.profile_loader.load_profile`.

Project-local overrides win when present, same precedence as roles/workflows/profiles. Operators
who want different checks edit `.jig/checks.yaml`; the orchestrator picks that up.

`jig.profile_loader.copy_profile_templates` also copies the shipped catalog into
`.jig/checks.yaml` at profile apply time. Operators get an editable file alongside their copied
profile/workflow YAMLs without having to look up the defaults path. Idempotent — if
`.jig/checks.yaml` already exists, the copy is skipped (matching the workflow-copy behavior).
The fallback to defaults still runs at load time, so projects that pre-date this change keep
working without a copy.

### The shipped catalog

`jig/defaults/checks.yaml`:

```yaml
checks:
  # Full-suite checks for the validate phase.
  pytest-all:
    type: scripted
    command: "uv run pytest -q"
    severity: required
    timeout_s: 900

  ruff-check:
    type: scripted
    command: "uv run ruff check ."
    severity: required
    timeout_s: 120

  mypy-strict:
    type: scripted
    command: "uv run mypy --strict src/"
    severity: required
    timeout_s: 300

  # Diff-scoped checks for the test + implement phases.
  pytest-diff-tests:
    type: scripted
    command: "python -m jig.check_helpers.pytest_diff green"
    severity: required
    timeout_s: 600

  pytest-new-tests-fail:
    type: scripted
    command: "python -m jig.check_helpers.pytest_diff red"
    severity: required
    timeout_s: 600
```

Reasoning on each:

- **`pytest-all`** — full suite at validate. Catches regressions in tests not touched this ticket
  and integration breaks. `uv run` so the project's pinned environment is used.
- **`ruff-check`** — lints everything. Cheap; lives at validate.
- **`mypy-strict`** — running on `src/` (not `src/ tests/`) intentionally. Two reasons: (1) modern
  Python convention is strict-typing on library/application code, looser on tests; (2) for
  agent-driven development, types serve double duty as machine-readable schema for the *next*
  agent that reads the code. Annotating `src/` pays back on every future agent run that touches
  the module; annotating `tests/` doesn't, because test agents typically *add new* test files
  rather than extending existing ones. Operators who want test-file type-checking can override
  the command in their project-local catalog. Running the same `mypy src/` invocation at both
  the dev phase (via `pytest-diff-tests` companion or a separate `mypy-strict` entry on
  implement) and validate phase eliminates the dev-validate mypy mismatch that caused the
  hn-cli `38efa7d7` integration-validate failure.
- **`pytest-diff-tests`** — green discipline. Runs `python -m jig.check_helpers.pytest_diff green`
  which (1) computes the diff vs. the ticket's branch base, (2) extracts added/modified test
  functions, (3) invokes `pytest -q <node-ids>`, (4) exits non-zero if any fail.
- **`pytest-new-tests-fail`** — red discipline. Same diff extraction, but exits non-zero if any of
  the new tests *pass*. Old tests in the same files are not checked.

### The diff-parsing helper

New module `jig/check_helpers/pytest_diff.py`. Invoked as `python -m jig.check_helpers.pytest_diff
<mode>` where `<mode>` is `red` or `green`.

```python
def main() -> int:
    mode = sys.argv[1]  # "red" or "green"
    base = _detect_base()  # e.g. ``git merge-base HEAD origin/develop``
    diff = subprocess.run(["git", "diff", "--unified=0", base, "HEAD", "--", "tests/", "**/test_*.py"], ...)
    node_ids = _extract_test_node_ids(diff)  # ["tests/test_foo.py::test_bar", ...]
    node_ids = _filter_baseline_markers(node_ids)  # skip tests marked # tdd-baseline
    if not node_ids:
        return 0  # no test-diff means no gate; pass through
    rc = subprocess.run(["uv", "run", "pytest", "-q", *node_ids], ...).returncode
    if mode == "red":
        # We want each test to fail. pytest returns non-zero when any fail.
        # Inverting: zero means at least one test passed, which is the failure
        # mode for red-discipline.
        return 0 if rc != 0 else 1
    return rc  # green: pass-through
```

Details:

- **Base detection.** The orchestrator runs each phase in a per-ticket worktree branched off the
  ticket's parent (usually `develop` or the prior ticket's branch). `git merge-base HEAD
  origin/develop` is the standard, but the helper also accepts `JIG_TICKET_BASE` env var as an
  override (the orchestrator sets it from `worktree.base_branch`).

- **Test function extraction.** Walk the unified diff. Track `+ def test_*` and `+ async def
  test_*` additions inside files matching `tests/**/test_*.py` or `tests/test_*.py`. The
  enclosing class for the function (if any) is the most-recent `+ class Test*` seen earlier in the
  same file's diff hunks, or the prior pytest collection's class assignment. Output node-ids in
  pytest format: `tests/path/test_x.py::TestY::test_z` or `tests/path/test_x.py::test_z`.

  Edge case: modified-but-not-added tests. A `def test_x` that already existed and got its body
  rewritten is treated as "new" for red/green purposes — its behavior is new even if its name
  isn't. The helper detects this by checking whether the function body has any `+` lines in the
  diff hunks.

- **Baseline markers.** A test function whose immediate preceding comment is `# tdd-baseline:
  <reason>` is excluded from the new-tests list. Same applies to a `@pytest.mark.tdd_baseline`
  decorator. Operators who legitimately have a green-at-commit test (e.g. testing existing
  scaffolding from a prior ticket) document why and the check skips it. Without a marker, the
  check fires.

- **Empty diff.** A test phase that adds no new test functions — common for phases that only edit
  conftest.py or fixtures — passes both modes vacuously (`return 0` on empty node_ids). This is
  intentional: red-discipline doesn't apply when there are no new tests to check.

### Workflow YAML changes

Three shipped workflows touched. Each adds `automated_checks` on the relevant phase, nothing else:

```yaml
# jig/defaults/workflows/feature-s.yaml
phases:
  - name: test
    role: test
    task_template: "Write tests for: {ticket_title}"
    acceptance_criteria: "Tests cover the specified behavior"
    automated_checks: [pytest-new-tests-fail]
  - name: implement
    role: dev
    task_template: "Implement the feature so tests pass for: {ticket_title}"
    acceptance_criteria: "All tests pass"
    automated_checks: [pytest-diff-tests, mypy-strict]
  - name: review
    role: review
    ...  # unchanged
  - name: validate
    role: validate
    task_template: "Validate the implementation for: {ticket_title}"
    acceptance_criteria: "All tests pass, linters clean"
    automated_checks: [pytest-all, ruff-check, mypy-strict]
```

Same pattern for `feature-s-full.yaml` and `default.yaml`. `feature-xs.yaml` has no test phase so
it gets `[pytest-diff-tests, mypy-strict]` on implement and the full validate set; no red-discipline
check (there are no tests). `bugfix.yaml` follows the same pattern as `feature-s` since bugfixes
typically include a regression test.

The dev phase running `mypy-strict src/` matches what `validate` runs, eliminating the
dev-validate mismatch that caused the hn-cli `38efa7d7` integration-validate failure. Cost is
~5-10s per dev phase on a typical project — well under the agent spawn cost.

### Acceptance-criteria string disposition

The `acceptance_criteria` prose stays. It's the agent's prompt context — what the agent is told
to aim for. The `automated_checks` field is the orchestrator's gate — what the agent is *required*
to produce. The two complement: prose guides, gate enforces.

## Interfaces

### `python -m jig.check_helpers.pytest_diff <mode>`

CLI invocation used by the scripted check entries. Two positional values:

- `green` — exit 0 if all new-or-modified tests in the diff pass; non-zero otherwise. Used by
  the `pytest-diff-tests` catalog entry.
- `red` — exit 0 if all new-or-modified tests in the diff fail; non-zero if any pass. Used by
  `pytest-new-tests-fail`.

Reads `JIG_TICKET_BASE` env var for the base branch; falls back to `git merge-base HEAD
origin/develop` when unset.

Exits 0 vacuously when the diff has no new test functions (the gate doesn't fire on phases that
don't add tests).

### `jig/defaults/checks.yaml`

Shipped catalog. Operators override per-project by writing `.jig/checks.yaml`.

### `load_check_catalog(project_path)` fallback

Updated to read `.jig/checks.yaml` first; if missing or empty, read
`jig/defaults/checks.yaml`. Maintains existing return shape and validation semantics.

## Data model

No schema changes. Existing `Check` discriminated union (`scripted` /
`implementation_aware_agent` / `black_box_agent`) covers all new entries — they're all
`scripted`. `PhaseConfig.automated_checks` is already a `list[str]`.

## Alternatives considered

### Run pytest with `--collect-only` and pattern-match the diff against collected node-ids

This would catch tests that moved files or got renamed — a `git diff` walk only sees the new
function-name line. Tradeoff: requires running pytest's full collection step, which is expensive
on large projects (the cost we're trying to avoid). Stuck with diff-walk; renamed tests count as
add+delete and the new name gets gated normally.

### Use `pytest --testmon` for impact analysis on the implement phase

Tempting: testmon could identify which tests cover the changed source files. But it requires a
test database to bootstrap and is heavy to set up. The diff-of-test-files heuristic is simpler
and the validate phase catches anything diff-scope missed.

### Single catalog entry that takes a mode parameter

Per the `Check` schema, `command` is a fixed string. Parameterizing via env var or argv is fine,
and the design uses argv. The alternative (two near-identical catalog entries with different
mode flags) is what's drafted because each entry maps to one phase semantically — clearer than
sharing a name and overloading mode.

### Skip red-discipline entirely; just enforce green at implement

Loses the leverage on fixture-correctness gaps like `--type ask/show`. Red discipline is the
specific lever that forces the test author to confront *what failure means*. Worth the friction.

## Risks

- **Slow pytest startup on large projects.** `uv run pytest <node-ids>` still pays the pytest
  import cost (~1-2s). On a 10K-test project that's negligible relative to the agent spawn cost.
  On a 100-test project it's a measurable fraction. Acceptable.

- **`git merge-base` requires a remote ref.** The orchestrator's worktrees are local; `origin/develop`
  may not be present in every environment. `JIG_TICKET_BASE` env var is the primary path; the
  fallback to `origin/develop` is the convenience case. Worktree creation already records the
  base branch in `worktree.base_branch`; the orchestrator can plumb that into the check
  environment via `extra_env`.

- **`mypy --strict src/` requires a `src/` layout.** Most jig project templates use `src/`, but
  not all. The catalog command is a default; projects with non-`src` layout override locally.

- **`# tdd-baseline` markers as an escape hatch.** Could be overused to silence the red-discipline
  check. Mitigation: the marker requires a `: <reason>` suffix, and the reviewer-generalist at
  the review phase can flag suspicious clusters. Not solvable in the gate itself; this is a
  social discipline concern.

## Out of scope

- The `reviewer-test-quality` role for judgment-level test concerns. Separate feature, separate PR.
- Per-ticket coverage threshold checks.
- Mutation testing.
- Hooking `pytest-testmon` for true impact analysis.
- Customizing the catalog per-profile (e.g. medium gets stricter mypy than small). Profile YAMLs
  don't currently reference catalogs; that interaction can come later if useful.
- Changes to `acceptance_criteria` string prose — agent prompts are not touched.

## Open questions

_All resolved 2026-05-22 (see Change log)._

## Change log

- 2026-05-22: Initial draft (brent)
- 2026-05-22: Resolved open questions. Catalog copies into `.jig/checks.yaml` at profile apply
  (alongside profiles + workflows) so operators get an editable file by default; runtime fallback
  to defaults stays as a safety net for projects that pre-date the copy. Dev phase gets
  `mypy-strict` to match validate (eliminates the dev-validate mismatch from hn-cli `38efa7d7`).
