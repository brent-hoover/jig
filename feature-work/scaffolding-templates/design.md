---
title: Scaffolding via Templates — Design
type: design
status: draft
owner: brent
created: 2026-05-24
updated: 2026-05-24
problem: ./problem.md
---

# Scaffolding via Templates — Design

## Summary

Two changes, intended to land as two separate PRs in this order:

1. **Remove the `feature-xs` workflow.** Profiles that referenced it map `xs` to `feature-s` (which has a `test` phase).
   Workflow loader gains an explicit alias so any existing-project tickets that still reference `feature-xs` resolve to
   `feature-s` rather than 404. PM's "xs" size value persists but means "small feature ticket with a test phase," not "skip
   testing."
2. **Ship the `python-cli` template** at `jig/defaults/project_templates/python-cli/`. Template carries an opinionated typer
   CLI entry, an injectable `httpx.AsyncClient`-style transport, ruff/mypy/pytest config (inherited from `python`), and a
   working smoke test (`--help` runs cleanly). SA role gains a small shape-detection rule that picks `python-cli` over the
   bare `python` template when the brief implies a CLI. CI gets a step that runs each bundled template's smoke test.

Both changes are local. Existing projects keep working. After PR 1, every new project's first feature ticket goes through a
workflow with a `test` phase. After PR 2, the `python-cli` shell stops being agent-authored on every CLI eval run.

## Approach

### Part 1 — Remove `feature-xs`

**Files touched:**

- `jig/defaults/workflows/feature-xs.yaml` — delete.
- `jig/defaults/profiles/small.yaml` — `default_by_size.xs: feature-s`; remove `feature-xs` from `available`.
- `jig/defaults/profiles/medium.yaml` — same change; description prose mentioning xs gets updated.
- `jig/defaults/work_types/feature.yaml` — `xs: feature-s`. The `# implement → validate only` comment is removed.
- `jig/defaults/roles/reviewer_generalist.yaml` — description mentions `feature-xs` in the workflow list; drop.
- `jig/reviewers/dispatch.py` — `feature-xs` in the workflow allowlist comment is dropped (verify it's only a comment, no
  active dispatch logic; if there is dispatch logic, it loses one branch).
- `jig/defaults/roles/pm.yaml` — the xs sizing rule (line ~206) is rephrased: xs still exists as a size but means "small
  scoped change with a test phase," not "skip TDD."

**Workflow-loader alias.** Where workflows are loaded from disk (`workflow_loader` or similar — exact site to be confirmed
during plan), add a static alias map: `{"feature-xs": "feature-s"}`. Loader resolves through the alias before reading from
disk. Any existing-project ticket persisted with `workflow: feature-xs` resolves transparently to `feature-s` and runs the
full pipeline (including the test phase that the ticket was originally created without). The alias is logged at WARN level on
first resolution per session so operators see the rewrite happen.

Aliases are removable. The plan doc will mark "delete the alias entry" as the cleanup step for a future PR once we have data
that no live projects reference `feature-xs` anymore.

**PM sizing implications.** PM today emits `size: xs` for small tickets and that's wired to a workflow without TDD. After
the alias, `xs` still means "small," but the resolved workflow has a test phase. PM's prompt doesn't need to know about the
remap — it keeps emitting `xs` for small work and the workflow loader does the right thing. No PM-role change required for
PR 1. The phrasing nit in `pm.yaml` (the comment line) is a docs-only update.

### Part 2 — Ship `python-cli` template

**Directory layout** (`jig/defaults/project_templates/python-cli/`):

```
template.yaml                      # metadata: language, framework=typer, deploy_target=null
pyproject.toml                     # inherits python template's config; adds typer + httpx deps;
                                   # [project.scripts] myproject = "myproject.cli:app"
                                   # so `myproject --help` works from the installed entry point
README.md                          # CLI-shape readme stub
.gitignore                         # same as python template
src/myproject/__init__.py
src/myproject/__main__.py          # python -m myproject entry → cli.app()
src/myproject/cli.py               # typer.Typer() app with a `hello` placeholder command
src/myproject/transport.py         # injectable httpx.AsyncClient wrapper (constructor takes client)
tests/__init__.py
tests/test_smoke.py                # asserts the typer app's --help runs cleanly (via
                                   # typer.testing.CliRunner so no install step needed)
```

The `[project.scripts]` entry is required — without it the `myproject --help` invocation the smoke test pattern assumes
won't work post-install. Using `typer.testing.CliRunner` in the smoke test side-steps the install step entirely, which is
faster and works without `uv sync` having created the script wrapper.

**Template parametrization.** Hard-code typer + httpx for MVP. The existing `_apply_template_files` substitutes `myproject`
→ project name; no other variables. Rationale: the SA already records `cli_framework: typer` and `http_client: httpx` as
architectural decisions on the hn-cli eval — those are the right defaults. If a project genuinely needs `click` or
`requests`, the operator picks a different template (which doesn't exist yet — but the design doesn't preclude adding
`python-cli-click` later if a real case appears). Parametrization adds template-engine complexity for a flexibility no
current eval needs.

This means the SA's `cli_framework` / `http_client` decisions become ceremonial when `python-cli` is picked (the template
already made them). That's acceptable — the SA still records them in `architecture.yaml` for downstream agents to read.

**SA selection logic.** SA role prompt gets a shape-detection rule appended:

> If the brief implies a command-line tool (mentions "CLI," "command," uses imperative subcommands like "search," "filter,"
> "list," or describes input flags and stdout/file output as the primary interface), pick `python-cli`. Otherwise pick
> `python`. When uncertain, prefer `python` and explain in the rationale.

No new tool calls, no metadata-driven template registry. The SA already enumerates templates via `arch_list_templates()` and
calls `sa_propose_scaffold(template_name, ...)`. The choice is a prompt-level instruction.

**Smoke test CI.** No new CI job. A pytest-collected test at `tests/test_template_smoke.py` iterates each directory under
`jig/defaults/project_templates/`, scaffolds it into `tmp_path` via `_apply_template_files`, then runs the template's whole
test suite via subprocess (`uv sync && uv run pytest -q` inside the scaffolded project). Asserts exit code 0. Runs
automatically as part of the existing `pytest tests/` step in `.github/workflows/ci.yml` — no workflow YAML changes needed.

**Template contract:** each template MUST ship at least one collectable test under `tests/` (any filename) that asserts a
basic property (the existing `fastapi` template's `tests/test_health.py` is the model). The bare `python` template ships an
empty `tests/__init__.py` today — this design adds a `tests/test_smoke.py` to it that does the minimum (`import myproject`
succeeds) so the loop has something to collect. Templates with no tests would cause `pytest` to exit 5 ("no tests
collected"), which the runner treats as a failure — intentional, to force every shipped template to declare what
"successfully scaffolded" means for it.

One pytest-collection caveat: the smoke tests inside template directories (`jig/defaults/project_templates/*/tests/*.py`)
would otherwise be collected by the main test run with the wrong project name. The plan doc handles this with a
`norecursedirs` entry pointing at `jig/defaults/project_templates/`.

### Interactions between Part 1 and Part 2

Independent. PR 1 can land before PR 2 is even written — the python-cli template still wires its first feature ticket
through `feature-s` after Part 1, but the shell is still agent-authored until Part 2 lands. PR 2 in the other order also
works: the python-cli template ships, but if Part 1 hasn't landed, the first feature ticket (now thinner because the shell
is templated) still runs feature-xs with no tests.

PR-1-first is the recommended order because Part 1 closes the user-facing "no tests on foundation" complaint immediately;
Part 2 is then the cost/quality improvement on top.

## Interfaces

**Profile YAML.** Existing schema — `default_by_size` and `available` are existing fields. No new fields.

**Workflow alias.** New: a static dict in the workflow loader module mapping deprecated workflow names to current ones.
Not exposed as configuration; baked into the loader. Adding/removing entries is a code change.

**Template directory contract.** Existing — each template directory under `project_templates/` carries a `template.yaml`
with `name`, `description`, `language`, `framework`, `deploy_target`, `package_manager`. The smoke-test CI extends this
implicitly: every template SHOULD have a `tests/test_smoke.py` that exits 0 after a fresh `_apply_template_files`. The
contract is "if you ship a template, ship a smoke test." Templates without one are caught by the CI step (build fails).

**SA selection.** No new interface. SA continues to call `sa_propose_scaffold(template_name, ...)` with one of the names
returned by `arch_list_templates()`. Behavior change is prompt-only.

## Data model

No schema changes. `architecture.yaml` already records `template`, `template_applied_at`, `language`, `framework`,
`deploy_target`. With the new template these fields populate with `template: python-cli`, `framework: typer`. Downstream
readers (analyzer, reviewers, role-prompt loaders) consume them unchanged.

## Alternatives considered

### Alternative 1 — Don't delete `feature-xs`, just remove its use

Leave the workflow file in place but stop having any profile reference it. Pro: less risky for existing projects (no
loader change needed). Con: dead code, will rot, someone reintroduces it accidentally. The workflow alias gives us the
graceful-fallback property without keeping the dead code around.

### Alternative 2 — Migrate to cookiecutter/Copier

Replace `_apply_template_files` with a real templating library. Pro: well-tested ecosystem, supports prompts and hooks. Con:
adds a runtime dependency, requires migrating the existing `python` template, doesn't solve a problem the current MVP has
(name substitution is sufficient for both bundled templates). Documented as a non-goal in problem.md; reconfirmed here.
Revisit when a template needs branching logic or interactive prompts.

### Alternative 3 — Make the template fully parametric (typer-vs-click, httpx-vs-requests, etc.)

Template would prompt the SA for choices. Pro: flexible. Con: requires a templating engine (per Alt 2), and the SA today
makes those decisions in `decisions: {cli_framework, http_client}` already — duplicating the choice in template prompts is
ceremony. If a `click` user appears, the answer is a separate `python-cli-click` template, not a parametric one. Cost is
linear in template count, but bounded.

### Alternative 4 — Make `xs` mean something useful (validation-only tickets) instead of deleting it

Repurpose `feature-xs` as the workflow for tickets that genuinely don't need TDD — typo fixes, dependency bumps, format-only
changes. Pro: the size tier still exists for legitimate "this is too small for TDD" work. Con: the current xs is broken
*specifically* because the PM uses it for the first feature ticket, where TDD matters most. The kinds of tickets that
legitimately skip TDD already have workflows (`refactor`, `chore`, future `bump`). Adding xs as a synonym for "no-test"
preserves the foot-gun that prompted this work. Rejected.

### Chosen: delete `feature-xs`, alias for compatibility, ship one strong template

The two-PR split keeps blast radius small per change and lets us validate Part 1 in isolation. The alias preserves
existing-project behavior. The new template is one directory + one CI step.

## Risks

- **PM may still produce `feature-xs` references** if any cached prompt context or persisted ticket retains the workflow
  name. The alias mitigates but doesn't eliminate — if a ticket is created with `workflow: feature-xs` and the alias is later
  removed (planned future cleanup), that ticket breaks. Mitigation: WARN-log every alias resolution; check logs before
  removing the alias.
- **Template smoke test breaks on Python version drift.** If CI tests against Python 3.12 but a user has 3.13 with a typer
  incompatibility, scaffold succeeds but their first `pytest` run fails. Mitigation: pin Python version in CI matrix to
  match what the template targets (`requires-python = ">=3.12"`).
- **SA picks `python-cli` for a non-CLI project** because the brief is ambiguous. Operator can `swap` to re-prompt, but the
  swap costs a re-spawn (~$0.10–0.20, ~30s). Acceptable cost for now; if it becomes routine, revisit the selection rule.
- **The python-cli template's opinions become wrong for a real project.** If hn-cli works great but the next CLI eval needs
  `prompt_toolkit` instead of stdout, the template is no longer a fit. Answer: don't try to use `python-cli` for that
  project — add a new template or use bare `python`. The catalog accretes; templates are not promised to fit every CLI.
- **Workflow alias hides bugs.** A bug that would have manifested as "feature-xs workflow not found" now silently rewrites
  to feature-s. The WARN log is the mitigation; if it's noisy enough that it gets filtered, we lose visibility. Plan doc
  should ensure the log line is structured and grep-able.

## Out of scope

- Migrating `_apply_template_files` to cookiecutter or Copier (separate problem if needed).
- Building any template beyond `python-cli` in this work. `python-web-api`, `python-library`, `node-cli`, etc. are deferred
  per problem.md's incremental-accretion policy.
- User-supplied templates from arbitrary directories or repos (trust-model question, separate problem).
- Template versioning / multi-version templates (single version per directory; bump by replacing).
- Removing the workflow alias. Stays in place until we have data that no live project references `feature-xs`.
- Re-running templates on existing projects (Copier-style update flow).
- Changing the PM's sizing logic beyond the comment fix in `pm.yaml`.

## Open questions

- [ ] **Where exactly does the workflow alias live?** `jig/workflow_loader.py` is the obvious site name but the current
  load path needs to be confirmed during plan (`jig/cli.py` and `jig/orchestrator.py` both load workflows; the alias has to
  apply at the read point, not later). Plan-doc question.
- [ ] **What does `python-cli`'s smoke test assert beyond `--help`?** Floor: `myproject --help` exits 0. Stretch: stub
  command produces stub output (proves the transport wires correctly). Plan-doc decision.
- [ ] **Does `tests/test_template_smoke.py` cache the scaffold + uv sync per session, or pay both costs per template every
  run?** Two templates today means ~2 × (copy + uv sync) per test run. Acceptable now; revisit if catalog grows.
- [ ] **Should the alias entry log to WARN once per session or every time?** Once-per-session reduces noise; every-time
  gives full audit. Lean once-per-session with a structured log line so grep finds it.

## Change log

- 2026-05-24: Initial draft (brent)
