---
title: Test Rigor — Implementation Plan
type: plan
status: draft
owner: brent
created: 2026-05-22
updated: 2026-05-22
design: ./design.md
---

# Test Rigor — Implementation Plan

## Overview

Foundation work: ship a check catalog, wire `automated_checks` into shipped workflows, add a
small diff-parsing helper, and route the catalog through profile-template-copy. Layered quality
reviewer is a follow-up PR.

One commit on `feat/test-rigor` covering ~10 files. The risk surface is the diff-parsing helper —
everything else is YAML + a small loader path.

## Preconditions

- [x] Problem + design approved (`status: active`).
- [x] Design open questions resolved (catalog copies via profile apply; dev phase gets `mypy-strict`).
- [x] Project-profiles foundation merged (PR #72) — provides `copy_profile_templates` to extend.

## Steps

### 1. Author `jig/defaults/checks.yaml`

**What:** New file with five entries:

```yaml
checks:
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

**Why:** The shipped catalog is the foundation everything else hangs off.

**Verify:** `load_check_catalog` from a fresh project (no `.jig/checks.yaml`) returns all five
names after step 2's fallback is in place.

### 2. Add defaults fallback to `load_check_catalog`

**What:** `jig/checks.py:82` — when `.jig/checks.yaml` is missing or empty, load
`jig/defaults/checks.yaml` instead. New helper `_defaults_checks_file()` mirroring the
`_role_path_shipped` / `_profile_path_shipped` pattern.

```python
def _defaults_checks_file() -> Path:
    return _defaults_dir() / "checks.yaml"

def load_check_catalog(project_path: Path) -> CheckCatalog:
    project_file = _checks_file(project_path)
    if project_file.is_file():
        raw = yaml.safe_load(project_file.read_text()) or {}
        checks_block = raw.get("checks") or {}
        if checks_block:
            return CheckCatalog.model_validate(checks_block)
    shipped = _defaults_checks_file()
    if shipped.is_file():
        raw = yaml.safe_load(shipped.read_text()) or {}
        return CheckCatalog.model_validate(raw.get("checks") or {})
    return CheckCatalog({})
```

**Why:** Without the fallback, every project would need to author its own catalog or copy the
shipped one before the gates fire. The fallback makes the gates work out-of-the-box.

**Verify:** Unit tests in `tests/test_checks.py`:
- Project with no `.jig/checks.yaml` → returns shipped catalog (5 entries).
- Project with empty `.jig/checks.yaml` → same.
- Project with `.jig/checks.yaml` defining only `pytest-all` → returns just that entry (override
  wins, no merge — matches the existing role/workflow/profile precedence semantics).

### 3. Add `jig/check_helpers/pytest_diff.py`

**What:** New module + `__main__.py` wrapper. Exports `main(argv: list[str]) -> int` that:

1. Parse `argv[1]` as mode (`red` or `green`).
2. Read `JIG_TICKET_BASE` env var; fall back to `git merge-base HEAD origin/develop`.
3. Run `git diff --unified=0 <base> HEAD -- 'tests/**/test_*.py' 'tests/test_*.py'`.
4. Walk the unified diff. Track `+ def test_*` and `+ async def test_*` additions, and enclosing
   `+ class Test*` for each. Output pytest node-ids in the format
   `tests/path/test_x.py::TestY::test_z` or `tests/path/test_x.py::test_z`.
5. Filter out tests marked with `# tdd-baseline: <reason>` immediately above the function, or
   `@pytest.mark.tdd_baseline` decorator.
6. Treat modified-but-not-added test bodies as "new" — any `+` line inside a `def test_*` body
   (in the diff hunks) counts.
7. If `node_ids` is empty, exit 0.
8. Invoke `uv run pytest -q <node-ids>` and capture exit code.
9. `mode == "green"`: return pytest's exit code directly.
10. `mode == "red"`: return 0 if pytest's exit was non-zero (all failed as required), else 1.

**Why:** Centralizes the diff parsing in Python so it can be unit-tested. A bash one-liner with
`git diff | grep` would be brittle (parsing test names from diff headers is non-trivial).

**Verify:** Unit tests in `tests/test_pytest_diff_helper.py`:
- Diff with one added `def test_foo` → node-id list `["tests/test_x.py::test_foo"]`.
- Diff with added `def test_bar` inside class `TestY` → `["tests/test_x.py::TestY::test_bar"]`.
- Diff with `# tdd-baseline: pre-scaffolded` above a new test → excluded from output.
- Diff with no test additions → empty list, helper exits 0.
- Mode-`red` returns 0 when pytest fails, 1 when pytest passes.
- Mode-`green` is pass-through.

### 4. Wire `automated_checks` into shipped workflows

**What:** Edit five workflow YAMLs to add the appropriate checks per phase:

- `jig/defaults/workflows/feature-s.yaml`:
  - `test` → `automated_checks: [pytest-new-tests-fail]`
  - `implement` → `automated_checks: [pytest-diff-tests, mypy-strict]`
  - `validate` → `automated_checks: [pytest-all, ruff-check, mypy-strict]`

- `jig/defaults/workflows/feature-s-full.yaml`: same as `feature-s`.

- `jig/defaults/workflows/feature-xs.yaml`: no test phase. Add
  `[pytest-diff-tests, mypy-strict]` to implement and the full set to validate.

- `jig/defaults/workflows/default.yaml`: same as `feature-s` for the three core phases (test /
  implement / validate); the spec / review-tests / document phases stay unchanged.

- `jig/defaults/workflows/bugfix.yaml`: same as `feature-s` since bugfixes typically include
  regression tests.

`canonicalize.yaml`, `validation.yaml`, `spike.yaml`, `perf.yaml`, `migration.yaml`, `docs.yaml`,
`project.yaml`, `refactor.yaml` are left alone — they don't follow the test/implement pattern or
have other reasons.

**Why:** The gates have to be declared on each phase to fire.

**Verify:** A `WorkflowConfig.model_validate(yaml.safe_load(...))` on each touched YAML parses
clean. Existing `validate_workflow_references` (`jig/config.py`) cross-checks the names against
the catalog at load time and won't error since step 2's catalog includes all five.

### 5. Plumb `JIG_TICKET_BASE` into the check execution environment

**What:** `jig/orchestrator.py` — where the orchestrator invokes `ScriptedRunner` (via
`run_handoff_gate`), pass `extra_env={"JIG_TICKET_BASE": worktree.base_branch}`. Need to thread
the base through the call site.

`ScriptedRunner` already accepts `extra_env`; the only change is the orchestrator setting it from
the ticket's worktree.

**Why:** The diff-parsing helper needs to know what to diff against. The worktree knows its base
branch; the helper doesn't have that context independently.

**Verify:** Unit test on `ScriptedRunner` invocation: pass `extra_env={"JIG_TICKET_BASE": "develop"}`;
assert the subprocess sees the env var.

### 6. Copy shipped catalog into `.jig/checks.yaml` at profile apply

**What:** `jig/profile_loader.py:copy_profile_templates` — extend to also copy
`jig/defaults/checks.yaml` to `.jig/checks.yaml` if the destination doesn't already exist.

```python
def copy_profile_templates(profile: Profile, project_path: Path) -> None:
    ...
    # existing profile + workflow copy logic
    ...

    dest_checks = _jig_dir(project_path) / "checks.yaml"
    if not dest_checks.is_file():
        src_checks = _defaults_dir() / "checks.yaml"
        if src_checks.is_file():
            shutil.copyfile(src_checks, dest_checks)
```

**Why:** Operators get a discoverable, editable file alongside their profile and workflow YAMLs.

**Verify:** Extend `tests/test_profile_loader.py::TestCopyProfileTemplates`:
- After `copy_profile_templates(small, tmp_path)`, `tmp_path / ".jig" / "checks.yaml"` exists and
  matches `jig/defaults/checks.yaml`.
- Operator edit (write a custom catalog at `.jig/checks.yaml` before re-apply) survives a
  subsequent `copy_profile_templates` call.

### 7. Update package-data + ensure shipping

**What:** `pyproject.toml` — verify `jig.defaults` already includes top-level YAMLs in package
data, or add a `"jig.defaults" = ["checks.yaml"]` entry. The existing entries are
`"jig.defaults.roles"`, `"jig.defaults.workflows"`, `"jig.defaults.work_types"`,
`"jig.defaults.profiles"`. Since `checks.yaml` lives at the `jig.defaults` package root (not in a
subpackage), it needs its own entry — `setuptools` doesn't automatically include non-Python files
at the package root.

**Why:** Without this the shipped catalog won't be in the wheel.

**Verify:** `uv run python -c "from jig.persistence import _defaults_dir; print((_defaults_dir() /
'checks.yaml').is_file())"` returns `True` after `uv sync`.

### 8. End-to-end test for the bounce path

**What:** New test in `tests/test_init_workflow_e2e.py` (or a new file
`test_test_rigor_e2e.py` if the existing file is overcrowded): a feature ticket goes through the
flow with a fake dev agent that *claims* success but leaves tests failing. Assert the orchestrator
bounces the handoff back to dev with a `rejection_reason` mentioning the failing checks.

Test fixture writes a project with a known-failing test in the worktree; the fake dev's success
status is invalidated by the gate; the next dispatch loop sees a rejected handoff and re-runs the
implement phase.

Mirror test for the test phase: fake test author commits a green-at-commit test (no impl to test
against, but somehow it passes — e.g., it tests existing scaffolding). Without a baseline marker
the gate bounces.

**Why:** The bounce mechanism exists but isn't exercised end-to-end today. These tests pin the
foundation invariant: "agents can't claim done if the gate fails."

**Verify:** Both tests pass; both assert the bounce sequence via the thread store's
`Handoff.acceptance_state == "rejected"` and `Handoff.rejection_reason` containing the relevant
check names.

### 9. Commit + PR

**What:** Single commit on `feat/test-rigor`. Conventional commit subject:

```
feat(checks): Wire automated_checks into shipped workflows for TDD discipline
```

Body:
- The hn-cli run findings that motivated the work.
- The catalog + workflow wiring.
- The diff-parsing helper for fast phase-level checks.
- The `mypy-strict` parity between dev and validate.
- File summary.

**Verify:** PR opened against `develop`, full Problem/Fix + Manual Test Steps + Checklist sections
per CLAUDE.md PR conventions.

## Rollback

Single commit, fully revertable. The orchestrator's handoff gate gracefully handles an empty
`automated_checks` list (returns pass-by-default) — so a revert removes the gates without
breaking anything mid-flight. Existing projects without `.jig/checks.yaml` would lose the shipped
fallback on revert but continue working as before the PR landed.

## Out of scope for this plan

- `reviewer-test-quality` role (the judgment-layer follow-up). Separate PR.
- Per-ticket coverage threshold checks.
- Mutation testing.
- Customizing the catalog per-profile.
- Extending `automated_checks` to non-feature workflows (canonicalize, etc.). Their phase
  structure is different; addressing them is its own conversation.

## Change log

- 2026-05-22: Initial draft (brent)
