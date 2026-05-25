---
title: Agent CLAUDE.md Injection — Design
type: design
status: active
owner: brent
created: 2026-05-25
updated: 2026-05-25
problem: ./problem.md
---

# Agent CLAUDE.md Injection — Design

## Summary

Inject two CLAUDE.md files into every agent spawn. A jig-shipped global file — optionally extended
with a per-role addendum (e.g. reviewer-specific vs developer-specific guidance) — is written into
the per-spawn `CLAUDE_CONFIG_DIR` alongside the existing `settings.json` and plugins install. A
project-specific file maintained at `<project>/.jig/CLAUDE.md` (created from a per-template starter
during scaffold) is copied into the worktree as `CLAUDE.md` on every spawn — overwriting any
project-committed `/CLAUDE.md` and marked uncommittable so it never ends up in a PR. When the
project file is missing, a minimal stub is written instead so the "jig wins" invariant holds even
for un-scaffolded projects.

## Approach

### New artifacts

1. **`jig/defaults/agent_claude_md.md`** — the global CLAUDE.md content. Authored as part of this
   feature. First-cut content covers jig MCP tools (ticket CRUD, comments, memory, commits), the
   ticket workflow (status transitions, AC invariant), commit-message style (conventional commits,
   no co-branding), comment conventions (default to none; why-not-what), and the orchestrator's
   "fail loudly, no graceful fallbacks" stance.

2. **`jig/defaults/roles/<role>/CLAUDE.md`** — optional per-role addendum, shipped with jig. Lets
   e.g. the reviewer carry different conventions than the developer. Concatenated onto the global
   file at spawn time when a file exists for the spawning role; roles without one get only the
   global. Empty / missing is the default — addendum files land role-by-role as the need surfaces.
   Project-level overrides are explicitly NOT supported (see Out of scope).

3. **`jig/defaults/project_templates/<name>/.jig/CLAUDE.md`** — one starter per shipped template
   (`python`, `python-cli`, `fastapi`). Each carries stack-specific notes (`uv run pytest`,
   `uvicorn` entry points, etc.) plus the same set of fill-in placeholders for the SA / human:
   high-level architecture, where the code lives, common pitfalls, deploy notes.

### Touched modules

4. **`jig/agent_config.py`** — `ensure_agent_config_dir()` gains a `role: str | None` parameter
   and a third write step (alongside `_write_settings` and `_write_plugin`): read the package's
   `agent_claude_md.md`, append `jig/defaults/roles/<role>/CLAUDE.md` if one exists (separator: a
   single blank line — role files own their own headings), and write the result to
   `<config_dir>/CLAUDE.md`. Already a per-spawn isolated directory; no concurrency concerns.

5. **`jig/worktree.py`** — new helper:

   ```python
   async def sync_project_claude_md(worktree_path: Path, project_path: Path) -> None:
       """Render <project>/.jig/CLAUDE.md into <worktree>/CLAUDE.md and mark it uncommittable.
       Writes a stub if the project source is missing."""
   ```

   Algorithm:
   - Short-circuit: if `worktree_path.resolve() == project_path.resolve()`, return without
     touching the filesystem. Some jig spawns (init / spec-generator / concierge) run with the
     real project root as their worktree; calling sync there would clobber the operator's
     checked-in root `CLAUDE.md` and the skip-worktree mark would hide the damage from
     `git status`.
   - Read `<project_path>/.jig/CLAUDE.md`. If missing, use `_MISSING_PROJECT_STUB`.
   - Write content atomically to `<worktree_path>/CLAUDE.md`.
   - Determine tracked-ness: `git -C <worktree> ls-files --error-unmatch CLAUDE.md`.
     - If tracked (the project committed a `/CLAUDE.md`): `git update-index --skip-worktree CLAUDE.md`.
     - If untracked: resolve the exclude path via
       `git -C <worktree> rev-parse --git-path info/exclude`. **Important:** `info/exclude` is
       shared across the main repo and all linked worktrees (git has no per-worktree exclude
       file — `info/exclude` is not in the per-worktree extension list). So this writes to the
       main `.git/info/exclude`, which means the operator's main checkout will also ignore
       `CLAUDE.md`. Acceptable concession: operators rarely want to track a root `CLAUDE.md`
       anyway, and can edit `info/exclude` manually if they do. We still need to resolve via
       `git rev-parse` rather than naive concatenation of `<worktree>/.git/info/exclude`, because
       in linked worktrees `.git` is a *file* (pointing at the per-worktree gitdir), not a
       directory — the naive path would not exist. Append `CLAUDE.md\n` idempotently (read,
       check for the line, append only if missing).
   - Both git commands logged on failure (warning) but never raised. The injected content is
     present either way; the only risk is a spurious modification appearing in `git status`.

6. **`jig/agent.py`** — pass `role=ctx.role` into the existing `ensure_agent_config_dir` call so
   the role addendum can be resolved. Also `await sync_project_claude_md(ctx.worktree_path,
   ctx.project_path)` immediately before the `ClaudeAgentOptions` construction. Single call
   site for each, same per-spawn lifecycle. The helper is async to keep its git subprocess
   calls off the event loop and match `worktree.py`'s module-wide convention (``_run_git`` via
   ``asyncio.create_subprocess_exec``).

### Lifecycle

```
Scaffold time (once per project, during SA phase):
  apply_scaffold(template_name=..., project_path=...)
    -> _apply_template_files(...)                              # existing — uses rglob, picks up .jig/CLAUDE.md
       -> writes <project>/.jig/CLAUDE.md from template

Spawn time (every agent on every ticket):
  Orchestrator -> spawn_agent(ctx)
    -> ensure_agent_config_dir(role=ctx.role, ...)             # EXTENDED — also writes global + role CLAUDE.md
       -> reads jig/defaults/agent_claude_md.md
       -> reads jig/defaults/roles/<role>/CLAUDE.md if present
       -> writes concatenation to <CLAUDE_CONFIG_DIR>/CLAUDE.md
    -> await sync_project_claude_md(worktree, project_path)    # NEW (async)
       -> writes <worktree>/CLAUDE.md
       -> marks it skip-worktree or .git/info/exclude
    -> SDK call (existing)
```

### Sandbox path mapping

No new bind mounts. Both injection targets are already inside mounted regions:

- `<CLAUDE_CONFIG_DIR>` is bind-mounted at `/jig/claude-config` (see `agent.py:677-689`).
- The worktree is bind-mounted at `/workspace`.

The agent reads `$CLAUDE_CONFIG_DIR/CLAUDE.md` (Claude Code default) and `<cwd>/CLAUDE.md` (also
default), where `cwd` is `/workspace`. Both files are visible without further work.

## Interfaces

### File contracts

| Path | Format | Owner | Lifetime |
|---|---|---|---|
| `jig/defaults/agent_claude_md.md` | Markdown, no frontmatter | jig maintainers | ships with package |
| `jig/defaults/roles/<role>/CLAUDE.md` | Markdown, no frontmatter, optional | jig maintainers | ships with package |
| `jig/defaults/project_templates/<n>/.jig/CLAUDE.md` | Markdown, no frontmatter, placeholders | jig maintainers | ships with package |
| `<project>/.jig/CLAUDE.md` | Markdown, no frontmatter | SA + humans | per-project, gitignored (under `.jig/`) |
| `<worktree>/CLAUDE.md` | Markdown, no frontmatter | jig (auto-generated each spawn) | per-spawn, never committed |
| `<CLAUDE_CONFIG_DIR>/CLAUDE.md` | Markdown, no frontmatter | jig (auto-generated each spawn) | per-spawn, isolated dir |

### Python API additions

```python
# jig/worktree.py
_MISSING_PROJECT_STUB: str = (
    "# Project Notes (jig-managed)\n\n"
    "No project-specific notes have been set yet. "
    "Add them at .jig/CLAUDE.md in the project root.\n"
)

async def sync_project_claude_md(worktree_path: Path, project_path: Path) -> None: ...
```

```python
# jig/agent_config.py
def ensure_agent_config_dir(
    *,
    skill_names: list[str] | None = None,
    sandbox_config_path: str | None = None,
    spawn_dir_name: str | None = None,
    role: str | None = None,                   # NEW
) -> Path: ...

def _write_global_claude_md(config_dir: Path, role: str | None) -> None:
    """Write <config_dir>/CLAUDE.md: shipped agent_claude_md.md, optionally with
    jig/defaults/roles/<role>/CLAUDE.md appended after a blank-line separator."""
```

No CLI surface changes, no new commands, no new flags.

## Data model

N/A — content-only files. No persistent structured state introduced by this feature.

## Alternatives considered

### System-prompt injection (no files)

Read both files at spawn, concatenate, append to the SDK's `system_prompt`. Rejected on three
grounds: (1) the user explicitly asked for actual CLAUDE.md files (mental model matters), (2)
agents can't `Read` a system prompt to re-inspect their context mid-run, (3) suppressing the
project's committed `/CLAUDE.md` would still require touching the worktree (Claude Code would
otherwise pick it up via cwd discovery), so we don't save the worktree write anyway.

### Per-role × per-project overrides (`<project>/.jig/roles/<role>/CLAUDE.md`)

Same shape as the per-role addendum but with project-level overrides. Rejected: multiplies the
authoring/maintenance surface (per-template × per-role combos), adds precedence rules, and the
near-term need is for jig-shipped role-specific conventions (reviewer vs developer), not
project-overridable ones. The mechanism could be added later by extending the same lookup path
without breaking the package-only contract.

### Operator-editable `~/.jig/CLAUDE.md`

Add a third source-of-truth that an operator can drop in to override or extend the global file.
Rejected as out-of-scope per the problem doc. No clear use case beyond "operator wants to tweak,"
and they can edit the shipped file in their checkout if they really need to. Adding the override
mechanism means precedence rules, merge semantics, and another path to keep in sync.

### Co-exist with the project's committed `/CLAUDE.md`

Let Claude Code load both the project-committed file AND the jig-injected one (the discovery
mechanism supports multiple). Rejected because the committed file is typically written for
interactive human Claude Code users and may contradict jig conventions or reference tools agents
don't have. "Jig wins" was decided in the problem doc.

### Bind-mount over a path in sandbox mode

Skip the worktree write entirely; bind-mount the project file over a path Claude Code reads.
Rejected because (a) doesn't apply to `--no-docker` spawns, (b) forks the implementation between
sandbox and non-sandbox paths, (c) still requires shadowing the project's committed `/CLAUDE.md`
somehow.

### Chosen: file-based with worktree overwrite

Same mechanism works in both sandbox and non-sandbox paths. Uses Claude Code's native CLAUDE.md
discovery — no SDK options or wrappers needed. Worktree is throwaway, so overwriting carries no
real cost. `git update-index --skip-worktree` plus `.git/info/exclude` handles the never-commit
invariant idiomatically. Two small file writes per spawn — negligible.

## Risks

- **Agent commits the overwritten CLAUDE.md anyway** if `skip-worktree` silently fails or the
  agent uses a command that bypasses the skip bit (e.g. an explicit `git add CLAUDE.md`). Mitigation:
  log a warning when the mark-uncommittable step fails, rely on jig's PR/review surface
  (`commit_worktree`, reviewers) to catch the bad commit. Acceptable residual risk — the worst
  case is one bad commit that's easy to revert.
- **Project's `.jig/` is gitignored, so `.jig/CLAUDE.md` isn't tracked in the project repo.** This
  is intentional (it's operator/SA state, not project source), but means it isn't auto-shared with
  collaborators. Operators who want sharing can `!.jig/CLAUDE.md` in their `.gitignore`. Noting as
  a design choice rather than a defect.
- **Global content drifts from reality** — references to jig MCP tools that get renamed/removed.
  Mitigation: tight enough surface to catch by review, and a smoke test that asserts the
  documented tool names are exported by the MCP server.
- **SDK behavior assumption unverified**: relies on the spawned Claude Code subprocess honoring
  `$CLAUDE_CONFIG_DIR/CLAUDE.md` discovery when `CLAUDE_CONFIG_DIR` is set via the SDK's env
  forwarding (non-sandbox) or `--setenv` (sandbox). Plan includes a smoke-test step that
  asserts the agent sees the injected content on its first turn.

## Out of scope

- Operator-level `~/.jig/CLAUDE.md` override file.
- Project-level overrides of the per-role addendum (`<project>/.jig/roles/<role>/CLAUDE.md`).
- Per-template × per-role starter combinations.
- Content-level merging of global + project files.
- Migration command for pre-existing projects (operator re-scaffolds or hand-creates).
- A dedicated generator agent / pipeline for project content (SA edits during normal work).
- Versioning or change-tracking of CLAUDE.md content beyond what git already provides.
- Making `<project>/.jig/CLAUDE.md` tracked in the project repo by default.

## Open questions

- [ ] Smoke-test plan needs to verify that `CLAUDE_CONFIG_DIR/CLAUDE.md` is actually loaded by
      Claude Code in both sandbox and non-sandbox spawn paths. Detail belongs in `plan.md`, but
      flagging here because a negative result invalidates the global-file half of the design.

## Change log

- 2026-05-25: Initial draft (brent)
- 2026-05-25: Added per-role addendum mechanism (package-shipped only, no project override) —
  reviewer / developer / etc. can carry distinct conventions concatenated onto the global file
  (brent)
- 2026-05-25: Documented the project-root short-circuit (roborev #160 HIGH — init / spec-gen /
  concierge spawns use project-root-as-worktree) and clarified that the per-worktree
  `info/exclude` is resolved via `git rev-parse --git-path` rather than a raw
  `<worktree>/.git/info/exclude` path (roborev #159 HIGH — linked worktrees have `.git` as a
  file, not a directory) (brent)
- 2026-05-25: Corrected info/exclude documentation — it is SHARED across the main repo and any
  linked worktrees (git has no per-worktree exclude file), not per-worktree. Concession
  documented in the algorithm section (roborev #162 LOW + Claude PR review) (brent)
- 2026-05-25: Switched `sync_project_claude_md` signature to `async def` and the lifecycle
  snippet to `await sync_project_claude_md(...)` to match the implementation (roborev #163 LOW)
  (brent)
- 2026-05-25: Hardened the per-role addendum lookup against path traversal — `role` is now
  validated by `validate_safe_path_segment` before being used in the addendum path
  (roborev #164 HIGH) (brent)
