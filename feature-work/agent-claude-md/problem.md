---
title: Agent CLAUDE.md Injection — Problem Statement
type: problem
status: draft
owner: brent
created: 2026-05-25
updated: 2026-05-25
---

# Agent CLAUDE.md Injection — Problem Statement

## Context

Agents spawned by jig run as Claude Code processes with `CLAUDE_CONFIG_DIR` pointed at a per-spawn,
jig-managed directory under `~/.jig/claude-agent-configs/<spawn>/` (see `jig/agent_config.py`). That
directory currently contains a `settings.json` and a `plugins/` install for jig's own skills, but no
`CLAUDE.md`. The agent's cwd is its git worktree at `<project>/.jig/worktrees/<id>/`.

Claude Code natively auto-discovers two kinds of `CLAUDE.md` files: a user-global one at
`$CLAUDE_CONFIG_DIR/CLAUDE.md`, and project-level ones found by walking up from cwd. Today jig
provides neither — the global slot is empty, and the only project-level file an agent ever sees is
whatever the project happens to have committed at its repo root. The jig-managed config dir
deliberately suppresses the operator's personal `~/.claude/CLAUDE.md` to keep personal hooks and
guidance out of agent context, but nothing replaces it.

## Problem

There is no consistent way to give jig agents either:

- **Orchestrator-level guidance** that applies to every agent on every project — e.g. how to use
  jig's MCP tools, ticket workflow conventions, commit-message style, comment conventions, the
  ticket-AC invariant, etc.
- **Project-specific guidance** that the agent needs to be effective in a particular codebase —
  where the code lives, how to run tests, common pitfalls, deployment notes.

Today, all of this gets crammed into the per-role system prompt or relies on whatever happens to be
in a project's committed `/CLAUDE.md`. The system prompt mixes role/phase instructions with
cross-cutting guidance and is awkward to evolve. A project's committed `/CLAUDE.md` (if any) is
written for interactive human Claude Code users and is not appropriate for jig agents — it may
reference tools or workflows agents don't have, or omit jig-specific conventions agents need.

## Simplest possible solution

Two CLAUDE.md files per agent spawn:

1. **Global** — a single file shipped with jig at `jig/defaults/agent_claude_md.md`. Written into
   the per-spawn `CLAUDE_CONFIG_DIR/CLAUDE.md` by the existing `ensure_agent_config_dir()` helper.
   Claude Code picks it up natively as the user-global CLAUDE.md.

2. **Project-specific** — lives at `<project>/.jig/CLAUDE.md`. Created from a template at scaffold
   time (each project template ships a starter), refined by the SA agent during its run, edited by
   humans afterward. On every agent spawn, copied into the worktree as `<worktree>/CLAUDE.md`,
   overwriting any project-committed `/CLAUDE.md` already there and marked uncommittable
   (`git update-index --skip-worktree` for tracked files, `.git/info/exclude` for untracked).

Both files refreshed on every spawn so edits propagate without manual invalidation. Cost is two
small file writes per spawn.

## Complications considered

- **Scale**: N/A — two small file writes per spawn; bounded by the agent spawn rate.
- **Concurrency**: N/A — each spawn already gets its own isolated `CLAUDE_CONFIG_DIR` and its own
  worktree. No shared mutable state, no races.
- **Failure modes**:
  - Global file missing from the package (shouldn't happen): log a warning, proceed without it
    rather than failing the spawn.
  - `<project>/.jig/CLAUDE.md` missing (project pre-dates this feature, or operator deleted it):
    write a minimal stub to `<worktree>/CLAUDE.md` ("jig-managed — no project notes set yet") so
    the "jig wins" invariant holds and the project's checked-in `/CLAUDE.md` is still suppressed.
    Spawn proceeds; the agent gets no project-specific guidance but is at least not loading content
    written for human Claude Code users.
  - `git update-index --skip-worktree` fails on the overwritten `<worktree>/CLAUDE.md`: log a
    warning. Tolerable risk: the file shows as modified and *could* be committed by an agent that
    `git add .`s indiscriminately. Mitigated by jig's existing commit-flow conventions.
- **Cross-cutting policies**: N/A — content-only files, no PII, no secrets, no auth.
- **Backwards-compatibility**: Existing projects scaffolded before this feature won't have a
  `.jig/CLAUDE.md`. No migration mechanism — jig has no production deployments, so operators either
  re-scaffold the project or drop the file in by hand. The missing-file behavior above means spawns
  still succeed against un-migrated projects.
- **Sandbox path mapping**: The jig CLAUDE_CONFIG_DIR is already bind-mounted into bwrap at
  `/jig/claude-config`, and the worktree is bind-mounted at `/workspace`. Both target locations
  for the new files are already inside mounted regions — no new mounts needed.

## Constraints

- Must work in both sandboxed (bwrap) and non-sandboxed (`--no-docker`) spawn paths.
- Must not pollute the project's tracked git state. The worktree's `CLAUDE.md` must never show as
  dirty in `git status` and must never get committed.
- Must integrate with the existing template / scaffold flow (`apply_scaffold`,
  `copy_profile_templates`) — no parallel mechanism for shipping the per-template starter.
- Must preserve the existing per-spawn isolation of `CLAUDE_CONFIG_DIR` (no shared mutable state
  across concurrent agents).
- No SDK changes — uses what `ClaudeAgentOptions` and the Claude Code CLAUDE.md discovery already
  offer.

## Requirements

- Every agent spawn sees a jig-authored global `CLAUDE.md` at `$CLAUDE_CONFIG_DIR/CLAUDE.md`.
  Real first-cut content authored as part of this feature (jig MCP tools, ticket workflow, AC
  invariant, commit style, comment conventions) — not a placeholder.
- When `<project>/.jig/CLAUDE.md` exists, every agent spawn sees its contents at
  `<worktree>/CLAUDE.md`.
- When `<project>/.jig/CLAUDE.md` is missing, the spawn still succeeds and a minimal stub is
  written to `<worktree>/CLAUDE.md` so the project's committed `/CLAUDE.md` (if any) is still
  suppressed.
- A project's own committed `/CLAUDE.md` (if any) is never loaded into agent context.
- The worktree's `CLAUDE.md` is never accidentally committed.
- Each shipped project template (`python`, `python-cli`, `fastapi`) ships its own
  `.jig/CLAUDE.md` starter with stack-specific placeholders (test commands, entry points, etc.) so
  newly-scaffolded projects have a useful starting point.
- Both files are refreshed on every spawn — no stale-cache rebuild step required.

## Non-goals

- No operator-level customization of the global file (no `~/.jig/CLAUDE.md` override mechanism).
  Operators who really want to tweak it edit the shipped file in their checkout.
- No project-level or operator-level overrides of the per-role addendum. Per-role addendums are
  package-shipped only (the design covers a single addendum slot at
  `jig/defaults/roles/<role>/CLAUDE.md` so e.g. reviewers can carry distinct guidance from
  developers). The original "no per-role variants" non-goal was relaxed during brainstorming —
  see the design's Alternatives section.
- No content-level merging of the global and project files. They remain two separate files in two
  separate locations and Claude Code loads both on its own.
- No touching the project's source repo — only the worktree and the CLAUDE_CONFIG_DIR.
- No layering with the project's committed `/CLAUDE.md` — we explicitly suppress it ("jig wins").
- No dedicated agent or pipeline whose job is to *generate* project-specific CLAUDE.md content.
  The SA may write or refine it during its normal run, but that's standard "agent edits a file"
  behavior, not a special generation step.

## Success criteria

- Spawning an agent against any project results in both `CLAUDE.md` files being present at the
  expected paths and visible to the agent.
- The project's committed `/CLAUDE.md` (if it exists) does not appear in the agent's loaded
  context — verifiable by inspecting what the agent sees on first turn.
- `git status` inside the worktree shows no spurious `CLAUDE.md` modification after a spawn.
- New projects scaffolded from any of the three current templates have a `.jig/CLAUDE.md` after
  `apply_scaffold` runs.

## Open questions

None. All initial open questions resolved during brainstorming (see Change log).

## Change log

- 2026-05-25: Initial draft (brent)
- 2026-05-25: Resolved open questions — missing-file behavior is "write minimal stub", no
  migration mechanism, per-template starter content, real global content drafted as part of this
  feature (brent)
- 2026-05-25: Relaxed "no per-role variants" non-goal to align with the per-role addendum
  decision in the design (roborev #159 LOW) (brent)
