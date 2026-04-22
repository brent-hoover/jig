# Jig Hooks — Design Spec

**Phase 5 Task I: Human-side git hooks**

## Goal

Give human contributors the same content checks the agent harness runs, so a developer's local commit/push experience matches what the orchestrator enforces. A `jig hooks install` command writes git hook scripts that invoke `jig hooks run <stage>`, which loads the project's check catalog and runs the appropriate subset for the stage. `jig init` installs hooks by default; `--no-hooks` opts out.

The hooks are a dev-loop convenience and an enforcement parity layer — not a record of truth. They never write to the `check_results` store; agent-driven runs remain the canonical record. Humans can still bypass with `git commit --no-verify` when they have to; that's git's contract and we don't try to break it.

## Non-Goals

- **No agent checks at hook time.** `implementation_aware_agent` and `black_box_agent` checks cost tokens and take seconds to minutes — wrong fit for `pre-commit`. Hooks run scripted checks only.
- **No `CheckResult` persistence from hook runs.** Hooks are dev-local; the harness owns the audit trail.
- **No new severity gates.** `pre-commit` reuses `severity: required` from the existing catalog. No new "hook-only" severity.
- **No per-hook install flags in v1.** `jig hooks install` installs all three or none. Per-hook opt-in is YAGNI until someone asks.
- **No shell-integration tests in v1.** Unit tests cover the install/uninstall lifecycle and the per-stage runners; we don't spin up real `git commit` invocations against installed hooks in CI.

## Architecture

```
.git/hooks/
├── pre-commit       # bash trampoline → 'jig hooks run pre-commit'
├── pre-push         # bash trampoline → 'jig hooks run pre-push'
└── commit-msg       # bash trampoline → 'jig hooks run commit-msg "$1"'

jig/
├── hooks.py         # install/uninstall/status + per-stage runners
└── cli.py           # 'hooks' Click subgroup; 'init' integration
```

The hook scripts are tiny bash trampolines that shell out to `jig hooks run <stage>`. All real logic lives in Python — the hooks themselves never need re-installing when the catalog or config changes.

### File layout

| Module | Responsibility |
|--------|----------------|
| `jig/hooks.py` | Hook install/uninstall/status; sentinel detection; backup management; per-stage runners (pre-commit / pre-push / commit-msg). |
| `jig/cli.py` | New `hooks` Click subgroup with `install`, `uninstall`, `status`, `run` subcommands. `init` calls `install_hooks()` after config save unless `--no-hooks`. |
| `jig/project.py` | Adds `HooksConfig` pydantic model with `pre_push_command: str \| None = None`. New `Project.hooks: HooksConfig = HooksConfig()` field. |
| `tests/test_hooks.py` | Unit + integration tests covering every state transition described below. |

The hook script templates live as string constants in `jig/hooks.py` so they ship with the package — no `defaults/hooks/` directory.

## CLI Surface

```
jig hooks install [--force]
jig hooks uninstall
jig hooks status
jig hooks run <stage>   # invoked by the hook scripts; not for humans

jig init [--no-hooks]   # opts out of the install step
```

### `jig hooks install [--force]`

Resolves the git common dir via `git rev-parse --git-common-dir` (works whether cwd is the main repo or a worktree). For each of `pre-commit`, `pre-push`, `commit-msg`:

1. Compute target path: `<common_dir>/hooks/<name>`.
2. **Target doesn't exist** → write hook script + `chmod 755` → report `"installed <name>"`.
3. **Target exists with sentinel** → overwrite silently (it's ours, refresh it) → report `"refreshed <name>"`.
4. **Target exists without sentinel:**
   - `<target>.jig-backup` doesn't exist → `mv target target.jig-backup`, write new hook, report `"installed <name> (existing hook backed up to .jig-backup)"`.
   - `<target>.jig-backup` already exists → refuse with clear error unless `--force`. With `--force`, overwrite the existing backup.

Exit 0 if every hook resolved cleanly; non-zero with a precise message on any refusal.

### `jig hooks uninstall`

For each hook name:

1. **Target missing** → report `"not installed: <name>"`, continue.
2. **Target exists without sentinel** → report `"skipped: <name> (not jig-managed)"`, leave it alone.
3. **Target exists with sentinel:**
   - `<target>.jig-backup` exists → `mv backup target` (restore), report `"uninstalled <name>, restored original from .jig-backup"`.
   - No backup → `rm target`, report `"uninstalled <name>"`.

Exit 0 once all three are processed (no fatal failures — non-jig hooks are reported, not errors).

### `jig hooks status`

Informational only; exit 0 regardless of state. One line per hook:

```
pre-commit: installed (jig-managed)
pre-push:   not installed
commit-msg: exists but not jig-managed — 'jig hooks install --force' to back up and replace
```

### `jig hooks run <stage>`

Invoked from the hook scripts. Click subcommand with `stage` constrained to `pre-commit | pre-push | commit-msg`. Per-stage logic in the next section.

### `jig init --no-hooks`

Skips the `install_hooks()` call. Without the flag, `init` invokes `install_hooks()` after `save_project()` (config file in place) and before the initial `chore: initialize jig project` commit (so the freshly-installed hooks don't gate the init commit on themselves).

## Hook Scripts

All three follow the same shape. Concrete `pre-commit`:

```bash
#!/usr/bin/env bash
# jig-managed hook — safe to remove via 'jig hooks uninstall'
# stage: pre-commit
set -e

if ! command -v jig >/dev/null 2>&1; then
  echo "jig hook: 'jig' command not found on PATH." >&2
  echo "Install jig or run 'jig hooks uninstall' to remove this hook." >&2
  exit 1
fi

exec jig hooks run pre-commit "$@"
```

`pre-push` differs only in the stage string and forwarding stdin (git pipes ref info on stdin to pre-push). `commit-msg` differs in the stage string and forwards `$1` (the path to the commit message file).

### Sentinel

The literal second-line string `# jig-managed hook — safe to remove via 'jig hooks uninstall'` is the sentinel install/uninstall greps for. We control the exact bytes we write, so the comparison is byte-equal on that line.

### `jig` not on PATH = hard fail

Silent skip would disable the safety net — defeats the task's whole point. The error names both ways out (install jig or uninstall the hook).

## Per-Stage Runner Logic

### `pre-commit` (global, all required scripted catalog checks)

1. Load `.jig/checks.yaml` via `load_check_catalog`.
2. Filter to `ScriptedCheck` entries with `severity == REQUIRED`.
3. Empty list → exit 0 with `"jig pre-commit: no required scripted checks; skipping"`.
4. Run each check sequentially via a small helper that mirrors the `ScriptedRunner` subprocess pattern but **does not** persist a `CheckResult`. Stream combined stdout/stderr to the user's terminal as each check runs.
5. **Run-all** (don't fast-fail). Collect every failure. After the loop, print a summary block naming the failed check(s) and the bypass instruction (`'git commit --no-verify' to bypass after fixing`).
6. Exit 1 if any check failed/timed out/errored; exit 0 otherwise.

Pre-commit stays global (not phase-aware) because it fires constantly and needs to be fast + predictable; lint/format/typecheck are relevant in every phase anyway.

### `pre-push` (phase-aware)

1. Resolve cwd's worktree root via `git rev-parse --show-toplevel`.
2. Detect "is this a jig ticket worktree?" — true iff the path matches `<project>/.jig/worktrees/<ticket_id>` exactly. Concretely: walk up from worktree root and verify `parent.name == "worktrees"` and `parent.parent.name == ".jig"`. The directory's basename is the candidate ticket id.
3. **In a ticket worktree:**
   - Load the ticket from `.jig/store/tickets.jsonl`. If not found → fall through to the "not in a worktree" branch.
   - Look up `current_phase`. If unset → exit 0 with `"jig pre-push: ticket has no current phase; skipping"`.
   - Resolve the ticket's workflow → phase config → `automated_checks` → catalog entries that are `ScriptedCheck`. Empty → no-op exit 0 with advisory.
   - Run them via the same helper as pre-commit. Same run-all + summary semantics.
4. **Not in a ticket worktree:**
   - Load `Project` from `.jig/config.yaml`. If `hooks.pre_push_command` is set, exec it via `/bin/sh -c` from the project root. Same exit semantics.
   - Unset → exit 0 with `"jig pre-push: hooks.pre_push_command not set; skipping"`.
5. **Ticket store missing or unreadable** → fall through to the "not in a worktree" branch silently. Pre-push is a courtesy gate; we don't surface store corruption at human-push time.

### `commit-msg` (always enforced when installed)

1. Read commit message from path passed as `$1`.
2. **Auto-bypass** if first line matches `^(Merge |Revert |fixup! |squash! )` — git-generated messages never go through the regex.
3. Otherwise apply: `^(feat|fix|chore|docs|test|refactor|perf|build|ci|style|revert)(\([^)]+\))?!?: .+`
4. Match → exit 0. Mismatch → print expected format + the rejected first line to stderr, exit 1.

Hardcoded type list. Hardcoded auto-bypass. No config knob — install/uninstall is the on/off switch.

## Configuration

One new field added to the `Project` model in `.jig/config.yaml`:

```yaml
hooks:
  pre_push_command: "uv run pytest -q"   # optional; unset → fallback no-op
```

`HooksConfig` pydantic model with one field for v1: `pre_push_command: str | None = None`. Defaults to `HooksConfig()` on the `Project` model so existing configs without a `hooks:` block round-trip cleanly.

No other configuration. Pre-commit reads the existing check catalog. Commit-msg's behavior is hardcoded. Pre-push inside a worktree consults the ticket/workflow state already in the store.

## Idempotency and Backups

| Starting state | `install` | `install --force` | `uninstall` |
|----------------|-----------|-------------------|-------------|
| No file | Write fresh | Write fresh | Report not installed |
| Jig-managed file | Refresh silently | Refresh silently | Remove (or restore backup if present) |
| Non-jig file, no backup | Move to `.jig-backup`, write new | Same | Skip (not ours) |
| Non-jig file, backup exists | **Refuse** with error | Overwrite backup, install | Skip (not ours) |
| Jig-managed file + backup | Refresh; backup untouched | Same | Restore backup → original |

Backup files are at `<common_dir>/hooks/<name>.jig-backup`. Permissions on the new hook are `0755` (executable for the owner-group-other triplet). Backup files preserve the original mode bits.

## Worktree Resolution Edge Cases

- `git worktree` lays down a `.git` *file* (not directory) in each worktree pointing at the common dir. Our resolution uses `git rev-parse --git-common-dir`, which handles both layouts.
- A user directory literally named `worktrees` somewhere in the path won't false-positive: the detector requires the parent to be `.jig/worktrees/`, the grandparent to be `.jig/`, and the great-grandparent to contain a `.git` entry.
- Ticket exists but `current_phase` is `None` (newly created, archived) → no-op + advisory line. Don't gate on undefined work.
- Workflow has no scripted checks for the current phase → no-op. Same posture: a phase that didn't declare scripted checks doesn't suddenly grow one at hook time.

## Testing

`tests/test_hooks.py` covers:

**Install/uninstall lifecycle** in a `tmp_path` git repo:
- Fresh install writes all three hooks with sentinel + `0755`.
- Re-install over jig-managed hooks → silent refresh, no backup created.
- Install over a non-jig hook → backup created, original content preserved byte-for-byte in `.jig-backup`.
- Install over a non-jig hook when `.jig-backup` already exists → refuses without `--force`; replaces backup with `--force`.
- Uninstall jig-managed hook with backup → restores backup contents.
- Uninstall jig-managed hook without backup → file removed.
- Uninstall non-jig hook → leaves it untouched.

**Status reporting** for each hook state: installed-jig-managed / not-installed / present-but-not-managed.

**`jig init --no-hooks`** writes no hook files; without the flag, hooks exist before the initial commit.

**Worktree resolution:** fixture creates `<root>/.jig/worktrees/<id>/` with a `.git` file, asserts resolver returns `<id>`; outside that exact pattern, asserts `None`.

**`jig hooks run pre-commit`:**
- Catalog with one passing + one failing scripted required check → exit 1, both checks ran, summary names the failure.
- Empty catalog → exit 0 + advisory line.
- Catalog with `severity: warning` checks only → exit 0 (warning never gates).
- Catalog with agent checks only → exit 0 (hooks never run agent checks).

**`jig hooks run pre-push`:**
- In a worktree fixture with a ticket whose current phase has scripted checks → runs them.
- In a worktree fixture with a ticket whose current phase has no scripted checks → no-op + advisory.
- In a worktree fixture but ticket missing from store → falls through to outside-worktree path.
- Outside a worktree, `hooks.pre_push_command` set → execs the command.
- Outside a worktree, `hooks.pre_push_command` unset → exit 0 + advisory.

**`jig hooks run commit-msg`:**
- Valid messages: `feat: x`, `fix(scope): y`, `chore!: z` (breaking marker).
- Invalid: `random subject`, empty, leading whitespace.
- Auto-bypass: `Merge branch 'main'`, `Revert "feat: x"`, `fixup! feat: y`, `squash! fix: z`.

End-to-end shell tests (real `git commit` against an installed hook) are deferred — slow and brittle in CI, and the unit tests cover the contract.

## Out of Scope

- Per-hook install flags (`--no-commit-msg` etc).
- Configurable commit-msg type list.
- Phase-aware pre-commit.
- Phase-aware commit-msg.
- Persisting hook runs as `CheckResult` records.
- Shell integration tests.
- A `jig hooks reinstall` shortcut (uninstall + install one-liner) — `install` already handles refresh.
