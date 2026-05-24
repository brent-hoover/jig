---
title: Scaffolding via Templates — Problem Statement
type: problem
status: draft
owner: brent
created: 2026-05-24
updated: 2026-05-24
---

# Scaffolding via Templates — Problem Statement

## Context

Today the init flow already does template-based scaffolding. The sequence is:

1. PO conversation → spec generator → PM picks profile → SA proposes a template (`sa_propose_scaffold(template_name, ...)`)
   along with architectural decisions.
2. Operator confirms; `apply_scaffold` runs as a phase of init: copies the template files from
   `jig/defaults/project_templates/<name>/`, substitutes `myproject` for the sanitized project name, writes
   `.jig/spec/architecture.yaml` + `.jig/project.yaml` with the template name and applied-at timestamp, installs git hooks, and
   commits the scaffold as a baseline. All before any dev ticket runs.
3. PM creates the planning ticket, decomposes the spec into feature tickets. The first feature ticket gets sized `xs` (the
   default for "fill in the project-specific shell" work) and runs under the `feature-xs` workflow.

The bundled `python` template at `jig/defaults/project_templates/python/` ships: `pyproject.toml` (pytest + ruff + mypy strict
already configured), `.gitignore`, `README.md`, empty `src/myproject/__init__.py`, empty `tests/__init__.py`. It is
shape-agnostic — it does not include CLI scaffolding, web-API scaffolding, or any other project-shape-specific shell code.
File substitution is `myproject` → project-name only; there are no cookiecutter-style prompts.

`feature-xs` is `implement → review → validate` — no `test` phase, by explicit profile design
(`small.yaml`: *"The only workflow distinction is xs (no test phase) vs everything else"*).

Observed concretely on the `hn-cli-20260524T144910Z` eval run: scaffolding produced the templated baseline (pyproject + dirs +
configs). The first PM-created feature ticket then committed `feat(skeleton): add CLI entry point, API stub, and smoke tests`
— typer CLI app + httpx async client stub — under `feature-xs`, with no `test` phase before `implement`.

## Problem

There are two related seams in the current foundation-of-the-project flow:

1. **The first feature ticket on every new project skips TDD.** It gets sized `xs`, runs `feature-xs`, and the test phase is
   omitted by design. The user-visible result: every project's foundation — the most consequential code in the codebase — is
   the only code in the project not produced via the test → dev → review → validate loop. The violation of "no code without
   tests" lands precisely on the work that's most likely to be load-bearing later.

2. **Shape-specific shell code (typer entry, httpx client wire-up, FastAPI app object, etc.) is agent-authored on every run**
   because the bundled templates are shape-agnostic. For shapes jig sees repeatedly (CLI tools being the obvious first case),
   the agent re-invents a layout that has a single right answer. Variance in output is pure cost — every run re-derives a
   skeleton that should be templated.

The two seams meet at the same ticket: the first feature ticket is *both* the project-specific shell *and* the no-test
ticket. Fixing either seam in isolation leaves the other open; fixing both closes the foundation-of-project quality gap and
removes the agent-re-invents-shell cost in one motion.

## Simplest possible solution

Two changes:

1. **Delete the `feature-xs` workflow.** Profiles that named it pick a workflow that has a `test` phase (`feature-s` is the
   obvious replacement). One config edit per profile + workflow-loader cleanup.
2. **Ship one strong opinionated `python-cli` template** that includes the CLI shell code currently re-invented every run
   (typer entry, an injectable `httpx.AsyncClient`-style transport, a working smoke test). SA's template registry gains the
   new template; SA's selection logic picks it when the brief implies a CLI.

This isn't a one-liner, but it is the minimum two-part change that closes both seams for the project shape we have working
evals for today (CLI tools). Larger template-catalog work is deferred — see Non-goals.

## Complications considered

- **Scale**: N/A — scaffolding runs once per `jig init`. The new template is one more file tree under
  `jig/defaults/project_templates/`. The maintenance surface grows linearly with the number of templates we ship; this
  problem statement explicitly limits the immediate scope to one new template.

- **Concurrency**: N/A — scaffolding runs serialized during init, single-writer to the new project directory.

- **Failure modes**: A bad template (broken `pyproject.toml`, smoke test that doesn't run) would propagate into every project
  built from it. Templates are committed source and should be tested in CI like any other code path. The current copier
  (`_apply_template_files`) already does atomic write; the failure mode that needs to be added is "the template's smoke test
  fails immediately after scaffold" — should that block init? Probably yes, to fail loudly. Design-relevant.

- **Cross-cutting policies**: N/A immediately — templates are jig-bundled and reviewed-as-code. User-supplied templates raise
  a trust question but that is explicitly deferred (see Non-goals).

- **Backwards-compatibility with existing projects**: Already-initialized projects past scaffolding are unaffected — they
  have their pyproject and shell code already. The workflow change (`feature-xs` removal) does affect *new* tickets created
  on existing projects: if any of those existing projects produces a new `xs`-sized ticket, the workflow loader needs to map
  `xs` to `feature-s` (or whatever the replacement is) gracefully rather than 404'ing on the removed workflow name.

- **PM sizing behavior**: PM currently sizes "fill in the project-specific shell" tickets as `xs`. If templates absorb the
  shell code, the PM either stops creating that ticket entirely (because there is no longer shell work to do) or sizes
  whatever remains based on actual scope. Either way the PM's sizing heuristic should be re-examined for the post-template
  world. Design-relevant.

- **The xs workflow may have other users we haven't surfaced**: Audit needed — is `xs` used anywhere outside "first feature
  ticket"? If yes, deleting it has more blast radius than this problem statement assumes.

## Constraints

- Must integrate with the existing `jig init` flow without rewriting it. `apply_scaffold` and `_apply_template_files` are
  the load-bearing functions; extending them is fine, replacing them is out of scope.
- Cannot break currently-initialized projects (no migration of in-place projects). New flow applies to new `jig init` runs;
  workflow changes need a graceful fallback for any ticket that still references `xs`.
- The SA's existing template-selection role and convention-loading machinery stay in place. The new template plugs into the
  same registry the existing `python` template uses.
- No new external runtime dependencies for end-users beyond what the existing scaffolder already pulls in.
- The new template's shell code must be opinionated enough that hn-cli-shaped projects don't need any agent-authored shell
  work to be functional.

## Requirements

- The `feature-xs` workflow is removed; profiles that referenced it map to a workflow that includes a `test` phase.
- A new `python-cli` template ships at `jig/defaults/project_templates/python-cli/` containing CLI shell code (typer entry +
  injectable transport pattern + smoke test) that hn-cli-shaped projects can use unchanged as their foundation.
- SA can pick `python-cli` for briefs that imply a CLI shape; the existing `python` template remains for shapes the new
  template doesn't fit.
- The new template's smoke test runs cleanly immediately after scaffold (no missing imports, no failing test on a fresh
  `jig init`).
- For projects scaffolded with `python-cli`, no PM-created ticket is needed to "fill in the project-specific shell" before
  the first actual feature ticket can be worked.
- The `xs` ticket-size value either disappears from the sizing vocabulary or maps to a real workflow; no orphaned size that
  points at a removed workflow.

## Non-goals

- **Building a comprehensive multi-template catalog.** The only new template in scope for this work is `python-cli`. Future
  templates (`python-web-api`, `python-library`, `node-cli`, etc.) get added incrementally as new project shapes come up in
  real eval / user work. This problem does not need to enumerate or design that catalog.
- **Replacing the existing jig-specific template copier** with cookiecutter, Copier, or any other library. The current
  `_apply_template_files` does name substitution and is sufficient for the new template too. Migration to a third-party
  templating system is a separate problem if/when parametrization needs outgrow what we have.
- **Template-authoring tooling.** Users can write templates by adding directories under `jig/defaults/project_templates/` or
  the equivalent registry path; no new CLI / scaffold-the-scaffolder UX in scope here.
- **User-supplied templates from arbitrary repos.** The bundled set is the entire surface for this work. Allowing external
  template sources raises a trust-model question worth a separate problem doc.
- **Re-running / updating templates after scaffolding.** One-shot only; matches current behavior.
- **Languages other than Python.** Templates are Python-only for now. The architecture should not paint itself into a
  Python-only corner but multi-language support is not in scope.
- **Touching projects already past scaffolding.** New flow is for new `jig init` runs only.

## Success criteria

- Running `jig init` against an hn-cli-shaped brief produces a project where the typer CLI entry, httpx client transport
  pattern, and a passing smoke test all come from the `python-cli` template, not from an agent-authored first ticket.
- After scaffold, the PM's first feature ticket on that project is a *real feature ticket* (e.g., "fetch top stories,"
  "filter by score") that runs under a workflow with a `test` phase — not a shell-wiring ticket.
- A re-run of the `hn-cli` eval does not produce a `feat(skeleton)`-style commit on a feature ticket; the equivalent code is
  already in place from the template before any dev work.
- The `xs` ticket size + `feature-xs` workflow are gone from the codebase; no orphan references survive in roles, profiles,
  or workflow loaders.
- An audit of currently-bundled profiles confirms none still resolve to `feature-xs`.
- A deliberately-broken `python-cli` template (e.g. smoke test that fails) is caught by jig's existing CI before it reaches
  an init run.
- A re-run of the `hn-cli` eval after `python-cli` ships shows measurable improvement on at least one of: total init →
  first-feature-ticket duration, dev-agent turns spent on shell wire-up, or review cycles on foundation work. This is the
  signal that the wider template direction is paying off and the next shape (likely `python-web-api`) is worth scoping.
- If the hn-cli re-run shows no measurable improvement, we pause adding more templates and re-examine before scoping the
  next one. "Ship more templates" is gated on evidence the last one paid off, not on the assumption that more is better.

## Open questions

- [ ] **How opinionated should the `python-cli` template be on dependency choices?** Today the SA records `cli_framework:
  typer` and `http_client: httpx` as architectural decisions. If the template hard-codes those, the SA's role on those
  axes becomes ceremonial for CLI projects. Options: hard-code (template is opinionated, SA loses those decisions for CLIs),
  or parametrize (template takes a few template-vars, SA fills them — requires extending `_apply_template_files`).
- [ ] **What does the SA's selection logic look like when there are multiple plausible templates?** Today `python` is the
  only option, so there's nothing to disambiguate. Adding `python-cli` means the SA has to decide CLI-vs-not from the brief.
  If wrong, the operator can `swap` — but the heuristic matters because the swap costs a re-spawn.
- [ ] **Workflow-loader behavior when a tickets references a removed workflow.** If an existing project has any ticket
  that's still pending under `feature-xs`, what does jig do — map to `feature-s`, error out, log a warning? Design-relevant
  because it affects how we sequence the removal vs. existing-project handling.
- [ ] **Does the new template's smoke test count toward the "no code without tests" rule, or is the rule specifically about
  agent-authored code?** If the template ships a test, the template-authored code is tested by definition. The rule probably
  only bites agent-authored code, but worth stating explicitly.
- [ ] **What's in the `python-cli` template's smoke test, exactly?** A test that `--help` runs is the obvious floor; a test
  that a stub command produces stub output is also plausible. This is a design-level question but worth flagging since the
  smoke test sets the bar for what other templates ship.

## Change log

- 2026-05-24: Initial draft (brent)
