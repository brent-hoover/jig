---
title: Reviewer file-scoping and test-reviewer expansion — Design
type: design
status: active
owner: brent
created: 2026-05-22
updated: 2026-05-22  # approved by operator, bumped to active
problem: ./problem.md
---

# Reviewer file-scoping and test-reviewer expansion — Design

## Summary

Add a `reads_glob` field on `RoleConfig` (include-set + optional exclude-set) and route every reviewer's view
of the worktree through two new MCP tools — `reviewer_get_diff` and `reviewer_read_file` — that enforce the
glob at the source. Drop unrestricted `Read` and `Bash(git diff*)` from reviewer role configs so the MCP
tools are the only path to the diff and file content. Update the five non-test reviewer roles to use a
broad-but-test-free scope; update `reviewer-test-adequacy` to a test-only scope plus an expanded prompt
that covers consistency (fixture/conftest hygiene, import ordering) alongside adequacy. Reject
out-of-scope findings at the routing layer as a defence-in-depth check.

## Approach

### Layer 1 — `reads_glob` on RoleConfig

Add two fields to `RoleConfig` (`jig/models.py`):

```python
class RoleConfig(BaseModel):
    ...
    reads_glob: list[str] = Field(default_factory=list)
    reads_exclude: list[str] = Field(default_factory=list)
```

Both default to empty lists, which means "no scoping" (current behaviour preserved for non-reviewer roles).
A role with a non-empty `reads_glob` is in scoped-mode; the new MCP tools and catalog validators read from
these fields.

Glob syntax: standard `fnmatch` / `pathlib.PurePath.match` rules — `**/foo`, `src/**`, `pyproject.toml`. Paths
are matched against the project-relative POSIX form (forward slashes regardless of host OS).

### Layer 2 — MCP `reviewer_get_diff` tool

New tool registered in `jig/mcp_server.py` alongside the existing `reviewer_post_comment`, gated on the
role declaring `reviewer_get_diff` in its `allowed_tools`. Behaviour:

```python
@tool("reviewer_get_diff", "...", {"base": str, "scope": str})
async def reviewer_get_diff(args):
    base = args.get("base") or "HEAD~1"
    # Resolve the role's reads_glob from agent_cfg; build a pathspec list.
    pathspec = _glob_to_git_pathspec(agent_cfg.reads_glob, agent_cfg.reads_exclude)
    result = subprocess.run(
        ["git", "diff", f"{base}..HEAD", "--", *pathspec],
        cwd=worktree_path, capture_output=True, text=True,
    )
    return {"diff": result.stdout, "scope": pathspec}
```

The `scope` field in the response is informational so the reviewer prompt can show "diff scoped to: src/**, pyproject.toml, ..." in the rendered context. The `base` arg's default resolution chain is:

1. `args.get("base")` — explicit override from the reviewer (rare).
2. `ticket_base_ref` — passed by the orchestrator into the MCP
   server at spawn time, derived from `Project.default_branch`
   (matches the base used by `create_worktree`). This is the
   authoritative per-ticket diff base; without it the chain
   below can return wrong results on chained / fix-loop tickets.
3. `JIG_TICKET_BASE` env var (legacy fallback for scripted
   handoff-gate checks).
4. Branch probe through `origin/develop` → `develop` → `main` →
   `master` → `HEAD~1` (last-ditch for dev / CLI runs).

### Layer 3 — MCP `reviewer_read_file` tool

Mirrors `reviewer_get_diff` for arbitrary file reads. Path is validated against `reads_glob` + `reads_exclude` before opening:

```python
@tool("reviewer_read_file", "...", {"path": str})
async def reviewer_read_file(args):
    path = args["path"]
    if not _path_in_scope(path, agent_cfg.reads_glob, agent_cfg.reads_exclude):
        return {"error": f"path {path!r} is outside this reviewer's scope: {agent_cfg.reads_glob}"}
    return {"content": (worktree_path / path).read_text()}
```

Reviewers with `reads_glob` set drop `Read` from `allowed_tools` entirely and replace it with
`reviewer_read_file`. Reviewers without `reads_glob` (test-adequacy in its CURRENT form, sa, dev, etc.)
keep their existing `Read` / `Bash` access — the new fields are opt-in per role.

### Layer 4 — Reviewer role config updates

Non-test reviewers (`reviewer_pattern_conformance.yaml`, `reviewer_security.yaml`,
`reviewer_error_handling.yaml`, `reviewer_performance.yaml`, `reviewer_architectural.yaml`):

```yaml
reads_glob:
  - "src/**"
  - "jig/**"           # mirror src/ for non-src-layout projects
  - "pyproject.toml"
  - "uv.lock"
  - "Dockerfile"
  - "scripts/**"
  - "docs/**"
  - ".jig/spec/**"     # architecture.yaml + contracts.yaml (load-bearing)
reads_exclude:
  - "tests/**"
  - "**/conftest.py"
  - "**/test_*.py"
  - "**/*_test.py"
allowed_tools:
  - reviewer_read_file    # replaces Read
  - reviewer_get_diff     # replaces Bash(git diff*)
  - Bash(find .jig/spec*) # kept — narrow pattern, lets reviewers enumerate spec files
  - reviewer_post_comment
  - graph_consumers_of
  - mark_finding_resolved
```

(``reviewer-generalist`` gets the same shape — it's an LLM
judgment reviewer dispatched by smaller workflows and would
otherwise be an unscoped escape hatch.)

`reviewer_test_adequacy.yaml`:

```yaml
reads_glob:
  - "tests/**"
  - "**/conftest.py"
  - "**/test_*.py"
  - "**/*_test.py"
  - "pyproject.toml"     # pytest config + dev deps visibility
reads_exclude: []
allowed_tools:
  - reviewer_read_file
  - reviewer_get_diff
  - reviewer_post_comment
  - mark_finding_resolved
```

### Layer 5 — Prompt updates

Each non-test reviewer's role.yaml `phase_prompt` gets a new "## Out of scope" stanza pinned at the top:

> Test files (anything under `tests/**` or matching `conftest.py` / `test_*.py` / `*_test.py`) are NOT
> in your scope. The `reviewer_get_diff` and `reviewer_read_file` tools filter them out of your view —
> attempting to fetch one returns a scope error. Do not spend turns probing for test-side issues. If
> you notice something that looks test-related while looking at non-test files, trust
> `reviewer-test-adequacy` to surface it on its own pass — do NOT file a finding on a test file.

`reviewer_test_adequacy.yaml`'s `phase_prompt` gets two named subsections under "## What you flag":

- `### Section 1 — Adequacy` (existing content: AC-driven coverage gaps, mock correctness, silent skips,
  asyncio_mode / fixture wiring)
- `### Section 2 — Consistency` (new content absorbing what other reviewers used to opportunistically
  cover: duplicated helpers that belong in conftest, BASE_URL re-definitions, import ordering in test
  files, `@pytest.mark` consistency, parametrize id consistency)

Both sections file findings via `reviewer_post_comment` as today — the only change is that the prompt
explicitly names both halves so the LLM doesn't drift away from consistency under the gravity of the
adequacy section.

### Layer 6 — Routing-layer defence-in-depth

`jig/reviewer_routing.py:_route_one` already inspects each finding's `file`. Add a pre-check: if the
file falls OUTSIDE the issuing reviewer's `reads_glob`, log a warning and treat the finding as a soft
error (don't route, don't bounce). This catches the cases where:

- An LLM hallucinates a finding on a file it never read.
- A role config typo opens up a wider scope than intended.

The orchestrator-side guard means a reviewer that somehow files an out-of-scope finding (despite the
MCP tools refusing reads) doesn't get to bounce the ticket on it.

### Layer 7 — Catalog validator

`jig/catalog.py` already cross-references workflow phases against role configs. Extend `validate_catalog`
to check:

- `reads_glob` entries parse as valid pathspecs (fnmatch syntax).
- For each reviewer with `reads_glob` set: `Read` and `Bash(git diff*)` are NOT in `allowed_tools`. This
  catches the "operator added reads_glob but forgot to drop the escape hatches" config error.
- `reviewer_get_diff` and `reviewer_read_file` ARE in `allowed_tools` when `reads_glob` is set.

## Interfaces

### `RoleConfig.reads_glob`, `RoleConfig.reads_exclude` (new fields)

Optional `list[str]` fields. Empty list = no scoping (back-compat). Glob entries match against
project-relative POSIX paths via `pathlib.PurePath.match` (`**`, `*`, `?`, character classes).

### MCP tool `reviewer_get_diff`

Args:
- `base: str` (optional, default = `JIG_TICKET_BASE` env or `HEAD~1`)

Returns:
- `{"diff": str, "scope": list[str]}` — unified diff plus the pathspec used.

Behaviour: shells out to `git diff` with `-- <pathspec>` derived from the role's `reads_glob` /
`reads_exclude`. Excludes apply via `:!` git pathspec magic (`:!tests/**`).

### MCP tool `reviewer_read_file`

Args:
- `path: str` — project-relative POSIX path.

Returns:
- `{"content": str}` on in-scope read.
- `{"error": str}` on out-of-scope path, with an explicit "your scope is: ..." message so the LLM can
  course-correct.

Behaviour: validates `path` matches `reads_glob` and does NOT match `reads_exclude`; reads via the
worktree-rooted path resolution; refuses absolute paths and any path containing `..`.

### Role-config YAML surface

New optional fields documented in `feature-work/reference/role_config.md` (or wherever role docs live).
Existing role configs without the fields work unchanged.

## Data model

No new persistent state. `reads_glob` lives in `RoleConfig` (YAML, already persistent). The MCP tools
are stateless query/read APIs.

## Alternatives considered

### Alternative 1 — Restrict tool patterns via SDK `allowed_tools`

The Claude Code SDK accepts `allowed_tools: ["Bash(git diff*)", ...]` and supports the `Tool(pattern)`
form. We could conceivably write `Read(src/**)` to scope `Read` directly. **Rejected** because:

- The `Read(<path-glob>)` syntax is undocumented in the SDK / CLI as far as I can find. We'd be
  building enforcement on a contract that may not exist.
- Even if it works, the matching is opaque (CLI-internal) — there's no way for the orchestrator to log
  or test what the reviewer can see.
- The MCP tool approach unifies diff + read enforcement under one mechanism we already operate.

### Alternative 2 — Path-scoped Bash only (`Bash(git diff -- src/**)`)

Keep `Bash` as the diff source but constrain its argument pattern. **Rejected** because:

- Pattern matching on a Bash arg string is brittle. The reviewer can write `git diff main..HEAD --
  src/foo.py` to match the prefix and then pipe through `grep`, or use `git log -p`, etc.
- We'd have to anticipate every variation an LLM might emit.
- MCP tools cannot be composed around — the only way to get the diff is the scoped tool.

### Alternative 3 — Per-reviewer divergent scopes

Each non-test reviewer gets its own custom `reads_glob` tuned to its domain (security sees `Dockerfile`
+ `uv.lock`; architectural sees `docs/architecture/**`; etc.). **Rejected for v1** because:

- Adds N config files to maintain in lockstep with the codebase layout.
- Creates subtle "reviewer X sees something reviewer Y doesn't" bugs where a finding on a shared file
  fires for one reviewer but isn't visible to another.
- We can graduate to divergent scopes later if uniform scoping proves too broad. For now, uniform
  non-test-file scope across all five non-test reviewers.

### Alternative 4 — Split test-adequacy into two reviewers

`reviewer-test-adequacy` (AC coverage) and `reviewer-test-consistency` (fixture / pattern hygiene). One
spawn per role per cycle. **Rejected** because:

- Doubles the spawn cost on every cycle.
- Two related concerns being in one prompt is normal; the issue with the current system was that the
  concerns were split across the WRONG reviewers (test consistency on a non-test reviewer). Fixing
  ownership doesn't require fragmenting test review.
- Single-prompt with two named sections is the well-trodden Anthropic guidance for this shape of task.

### Chosen: orchestrator-served MCP tools with RoleConfig glob

Lands the enforcement at the layer we already control (MCP server), uses an opaque-string config field
(`reads_glob`) that the catalog validator can check, and keeps the v1 implementation deliberately
uniform across non-test reviewers. Reviewer-test-adequacy expansion is a prompt-only change with no
spawn-cost increase.

## Risks

- **MCP tool roundtrip cost.** Each `reviewer_read_file` call is a tool-use → tool-result round trip,
  whereas inline `Read` is direct. For reviewers that need to read many files, this could add 10–50
  turns of overhead per cycle. Mitigation: the new `reviewer_get_diff` includes the WHOLE scoped diff
  in one call, which covers most reviewer use cases (most reviewers operate on the diff, not on
  arbitrary file reads). If file-read overhead proves real in eval runs, we can add a batch read tool.

- **Glob mismatch with on-disk layout.** Non-src-layout projects (jig itself uses `jig/` not `src/`)
  need both globs included by default. Operator override via `.jig/roles/reviewer-*.yaml` still works.
  The shipped default needs to handle both — the design above includes both `src/**` and `jig/**`.

- **Hallucinated findings on hidden files.** A reviewer could file a finding citing a file it never
  read (LLM confabulation). The routing-layer defence-in-depth catches this — out-of-scope file in a
  finding → soft-error log, no bounce. We do NOT silently accept the finding either; it surfaces in
  logs for operator review.

- **Test-adequacy prompt drift.** Adding the Consistency section may make the prompt longer and the
  Adequacy section's relative importance feel diluted, leading the LLM to skip thorough adequacy
  checks. Mitigation: explicit subsection numbering ("Section 1 — Adequacy", "Section 2 —
  Consistency") plus a closing instruction "file findings from BOTH sections before exiting; if you
  ran out of context, file the adequacy findings first."

- **MCP tool failures.** If the MCP server is misconfigured or the orchestrator can't compute the
  pathspec, the reviewer sees an error from its only diff source. Failing loud is correct here —
  silent fallback to unscoped diff would defeat the entire mechanism.

## Out of scope

- The forward-walk routing problem (when test bounce → test fix → orchestrator walks forward through
  dev instead of returning to the bouncing reviewer). Separate problem statement.

- Reducing the federation membership for `small` profile projects (analyzer rec #6). Separate problem.

- Carry-forward closure policy for `notable` findings (analyzer rec #4). Separate problem.

- Per-reviewer divergent scopes (alternative 3 above). Future enhancement if uniform scope proves
  insufficient.

- Splitting `reviewer-test-adequacy` into two reviewers (alternative 4 above).

## Open questions

- [ ] Should `reviewer_read_file` cache reads within a single agent session, or always re-shell?
  Caching saves git/disk I/O on repeated reads of the same file; the cache is per-spawn so no
  cross-cycle staleness concern.

- [ ] Does the routing-layer defence-in-depth (out-of-scope finding → soft-error) need an operator-
  visible signal beyond the log line? Possible: post a thread note "reviewer X filed N out-of-scope
  findings" so the operator can review.

- [ ] What's the right error message when `reviewer_read_file` refuses a path? "Path X is outside your
  scope" is honest but doesn't tell the reviewer what IS in scope. Echoing the full `reads_glob` is
  verbose. Compromise: return the scope summary plus a hint like "did you mean ... in scope?".

## Change log

- 2026-05-22: Initial draft (brent)
