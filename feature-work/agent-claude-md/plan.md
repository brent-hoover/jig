---
title: Agent CLAUDE.md Injection — Implementation Plan
type: plan
status: active
owner: brent
created: 2026-05-25
updated: 2026-05-25
design: ./design.md
---

# Agent CLAUDE.md Injection — Implementation Plan

## Overview

Smoke-test the load-path assumption first (no code), then ship in two PRs: one for mechanism (all
plumbing + tests + placeholder content) and one for content (real global + per-template starters +
per-role addendums). The split separates code correctness (objective, testable) from content
taste (subjective, easier to iterate on) without blowing scope into many small PRs.

## Preconditions

- [x] `problem.md` approved
- [x] `design.md` approved
- [ ] Work on a `.worktrees/` worktree branch off `develop` (per CLAUDE.md)
- [ ] Operator running smoke-test has a working `jig start` setup (Docker + bwrap or `--no-docker`)

## Steps

### 0. Smoke-test `CLAUDE_CONFIG_DIR/CLAUDE.md` load behavior (precondition, no PR)

**What:** Manually verify the load-path assumption before writing plumbing.

1. `mkdir -p /tmp/jig-claude-md-smoke && echo '# SENTINEL — visible' > /tmp/jig-claude-md-smoke/CLAUDE.md`.
2. Set `CLAUDE_CONFIG_DIR=/tmp/jig-claude-md-smoke` and spawn a one-shot Claude Code via the
   Agent SDK in a scratch script; `system_prompt` asks the model to report whether it sees the
   `SENTINEL` line in its CLAUDE.md context.
3. Repeat inside bwrap: bind-mount the dir, set the env via `--setenv`, repeat.

**Why:** If `$CLAUDE_CONFIG_DIR/CLAUDE.md` isn't actually loaded, the global-file half of the
design is invalidated. Catching that here costs ten minutes; catching it after the plumbing PR
lands wastes a week.

**Verify:** Agent's first response cites SENTINEL in both spawn modes. If not, stop and reopen
the design.

**References:** Design §Risks (SDK behavior assumption unverified).

### 1. Mechanism — PR 1

**What:**

- `jig/agent_config.py`:
  - Add `_write_global_claude_md(config_dir: Path, role: str | None) -> None`. Reads
    `jig/defaults/agent_claude_md.md` from package resources, appends
    `jig/defaults/roles/<role>/CLAUDE.md` (blank-line separator) when present.
  - Extend `ensure_agent_config_dir()` with `role: str | None = None`; call the new writer
    alongside `_write_settings` and `_write_plugin`.
- `jig/worktree.py`:
  - Add `sync_project_claude_md(worktree_path: Path, project_path: Path) -> None` and
    `_MISSING_PROJECT_STUB` constant. Reads `<project>/.jig/CLAUDE.md` (falls back to stub),
    atomic-writes to `<worktree>/CLAUDE.md`, marks uncommittable via `git update-index
    --skip-worktree` (tracked) or idempotent append to `.git/info/exclude` (untracked). Logs
    warnings on git failure; never raises.
- `jig/agent.py`:
  - Pass `role=ctx.role` to `ensure_agent_config_dir`.
  - Call `sync_project_claude_md(ctx.worktree_path, ctx.project_path)` immediately before
    `ClaudeAgentOptions` construction.
- Ship placeholder `jig/defaults/agent_claude_md.md` (`# jig agents — global CLAUDE.md\n\n(Real
  content lands in PR 2.)`) so the mechanism has something real to copy.

**Why:** Lands the entire injection mechanism as one coherent reviewable unit. With placeholder
content the behavior is inert in agent output but end-to-end testable.

**Verify:**

- `tests/test_agent_config.py` (new cases):
  - `_write_global_claude_md` writes expected content.
  - `role=None` produces global-only.
  - `role` with shipped addendum produces concat (global, blank line, addendum).
  - `role` without shipped addendum silently produces global-only.
- `tests/test_worktree.py` (new cases):
  - Source present → content copied verbatim.
  - Source missing → stub written.
  - Tracked CLAUDE.md → `skip-worktree` flag set (verifiable via `git ls-files -v`).
  - Untracked CLAUDE.md → entry appears once in `.git/info/exclude` after repeated calls.
  - `git status` clean in the worktree after the helper runs.
- `uv run pytest tests/test_agent_config.py tests/test_worktree.py -v` passes.
- `uv run ruff check jig/` and `uv run ruff format --check jig/` clean.
- Manual smoke against jig itself (which has a committed `/CLAUDE.md`): spawn an agent on a
  ticket, confirm via the agent's first turn that it cites the placeholder global content and
  that jig's repo `/CLAUDE.md` does not appear in its loaded context.

**References:** Design §Approach, §Touched modules #4–#6, §Interfaces.

### 2. Content — PR 2

**What:** Three batches of content files, no code changes:

- `jig/defaults/agent_claude_md.md` (replace placeholder): jig MCP tools, ticket workflow + AC
  invariant, commit conventions (conventional commits, no co-branding, WHY in body), comment
  conventions (default none; WHY-not-WHAT), failure stance (fail loudly, no graceful fallbacks
  for invariants).
- `jig/defaults/project_templates/<name>/.jig/CLAUDE.md` for each shipped template:
  - `python` — `uv run pytest`, package layout, placeholders for "where the code lives" / "common pitfalls".
  - `python-cli` — same shape, CLI-entry-point notes, `[project.scripts]` reference.
  - `fastapi` — `uvicorn`, async conventions, `httpx`/test client patterns.
- `jig/defaults/roles/<role>/CLAUDE.md` for the roles with concrete need. Use the runtime role
  IDs declared inside the yaml files' `role:` field (which are hyphenated), NOT the
  underscored filenames. `head jig/defaults/roles/reviewer_generalist.yaml` shows
  `role: reviewer-generalist`; `_write_global_claude_md` looks up addendums by the runtime
  role string, so the addendum directory name must match the hyphenated form. First-cut
  targets:
  - `dev` — coding stance, test-first preferences, scope discipline.
  - `reviewer-generalist` — finding-quality bar, IMPORTANT vs notable,
    no-flag-redundant patterns.
  - **Open question for PR 2**: should the seven `reviewer-*` roles
    (`reviewer-generalist`, `reviewer-architectural`, `reviewer-error-handling`,
    `reviewer-pattern-conformance`, `reviewer-performance`, `reviewer-security`,
    `reviewer-test-adequacy`) share content? Options:
    (a) ship one addendum per reviewer role (lots of duplication);
    (b) extend `_write_global_claude_md` with a prefix-fallback lookup
        (`reviewer-generalist` → fall back to `reviewer/CLAUDE.md`) — small code change;
    (c) ship only `reviewer-generalist` for now, leave the six specialists addendum-less.
    Decide before authoring the files. Default if undecided: (c) — minimal scope, defer the
    mechanism choice.

Other roles fall through to global-only — fine until concrete need surfaces.

**Why:** Plumbing is inert without content; content has nowhere to live without plumbing. Splitting
content from code keeps the mechanism PR small and lets content review focus on taste rather than
correctness.

**Verify:**

- `tests/test_agent_claude_md_content.py` (new):
  - Shipped global file mentions a set of known-stable MCP tool names (catches rename drift).
  - `dev` and `reviewer-generalist` addendums exist and are non-empty.
  - `_write_global_claude_md(role="reviewer-generalist")` produces output containing both global
    and reviewer content in the right order.
- `tests/test_apply_template_files.py` (new or extended): for each template, scaffolding writes
  `<dest>/.jig/CLAUDE.md` containing expected stack markers (e.g. `uv run pytest` for python,
  `uvicorn` for fastapi). Naturally also confirms `_apply_template_files` picks up `.jig/`
  dotdirs.
- `uv run pytest tests/test_agent_claude_md_content.py tests/test_apply_template_files.py -v`
  passes.
- Manual: spawn an agent, observe early actions respect documented conventions (commit-message
  style, no-graceful-fallback). Spawn a `reviewer-generalist` on a ticket, observe behavior
  aligns with the reviewer addendum. Run `jig init` against an empty project for each template,
  confirm `.jig/CLAUDE.md` shows up with the right starter content.

**References:** Design §Approach #1–#3, §Risks (content drift).

## Rollback

Both PRs independently revertible.

- Step 0 fails → no code merged; redesign required.
- PR 1 plumbing breaks spawns or pollutes worktrees → revert; `ensure_agent_config_dir` returns
  to its prior signature and `sync_project_claude_md` is removed. Agents resume seeing no global
  CLAUDE.md and the project's committed `/CLAUDE.md` (current behavior).
- PR 2 content issues → revert (or hot-fix individual files); PR 1's mechanism keeps working
  with the placeholder global content.

No production state, no migrations, no shared infrastructure. Worst-case operator impact is one
agent spawn cycle with a confusing CLAUDE.md, immediately fixable by reverting.

## Out of scope for this plan

- Project-level role overrides (`<project>/.jig/roles/<role>/CLAUDE.md`).
- Operator-level global override (`~/.jig/CLAUDE.md`).
- Migration command for pre-existing projects.
- Per-template × per-role starter combinations.
- Addendums for every role in `jig/defaults/roles/` — only the ones with concrete need.
- Promoting `<project>/.jig/CLAUDE.md` out from under `.jig/`'s gitignore.
- Any refactor of `_apply_template_files`, `ensure_agent_config_dir`, or `spawn_agent` beyond
  what these steps require.

## Change log

- 2026-05-25: Initial draft — 5 PRs (brent)
- 2026-05-25: Collapsed to 2 PRs (mechanism + content) per scope feedback (brent)
- 2026-05-25: Fixed role IDs (`dev`, `reviewer_generalist`) to match actual jig role names;
  flagged the seven `reviewer_*` roles as an open question for PR 2 (roborev #159 MEDIUM)
  (brent)
- 2026-05-25: Corrected role IDs again — runtime role strings inside the yaml files use
  hyphens (`reviewer-generalist`), not the underscored filenames. `_write_global_claude_md`
  looks up addendums by runtime role string, so the path naming must match
  (roborev #162 MEDIUM) (brent)
