---
title: v2 Security Review Prompt
type: review-prompt
status: ready
owner: brent
created: 2026-05-04
---

# v2 Security Review Prompt

Paste verbatim as the first message in a fresh agent session. The reviewer
needs read access to the repo and the ability to run `git` / `rg` / `pytest`.

---

You're doing a focused security review of jig (a multi-agent orchestrator)
on /Users/brent/Projects/personal/jig.

## Scope

The review target is every commit on `develop` ahead of `origin/develop` —
roughly 80 commits implementing v2. Baseline commit: 7b3d7f6. HEAD is on
develop.

Block 1 of an earlier review pass added a centralized safe-path primitive
(`jig/safe_path.py`) and applied it across id-derived paths. That fixed the
specific path-traversal findings the previous reviewer flagged. This review
is looking for what that pass missed.

## Threat model context

jig spawns Claude Code agents that:
- Run inside bwrap sandboxes (the design intent; verify the implementation)
- Get env vars injected by the orchestrator (`JIG_DEV_*_URL`, `JIG_FIXTURE_MODE`,
  `CLAUDE_CODE_OAUTH_TOKEN` indirectly via SDK auth)
- Talk to a local MCP server with strict-tools enforcement
- Read project files via Read/Grep/Glob and write via Write/Edit
- May execute arbitrary Bash commands when the role's `allowed_tools` includes Bash

The operator (human) drives via CLI. Agents and operators both author YAML/MD
files that flow into Pydantic schemas. External APIs (Shopify-style) are
mocked via vcr-style fixtures in jig/dev_env/fixtures.py.

## What to focus on (priority order)

1. **Agent-to-host escapes.** Can a malicious or confused agent:
   - Read or write outside its worktree?
   - Read environment variables not in its `extra_env` map?
   - Read other agents' worktrees in a multi-agent project?
   - Make subprocess calls that escape the sandbox?
   - Use git operations to leak credentials or modify the host config?

2. **MCP tool surface.** Every MCP handler in jig/*_mcp.py + jig/mcp_server.py
   takes agent-supplied arguments. Audit:
   - Argument validation: do any handlers trust an id string into a path?
   - Stored-credential exposure: do any handlers return env-var contents,
     OAuth tokens, or secret references?
   - Resource-bounding: can any handler write unbounded data to disk
     (DOS via storage)?
   - Cross-ticket access: can a ticket's agent read/write artifacts of
     another ticket?

3. **YAML/JSON parsing.** Multiple files load YAML from operator/agent-supplied
   sources. Audit:
   - `yaml.safe_load` vs `yaml.load` — find any unsafe loads
   - JSON parsing — `json.loads` of agent-supplied content
   - Pydantic model_validate — is `extra='forbid'` consistent?
   - Schema validation: does any path bypass schema validation and act on
     raw dict input?

4. **Subprocess invocations.** Audit every subprocess.run / subprocess.Popen /
   asyncio.create_subprocess_exec call:
   - Any with `shell=True`?
   - Any that interpolate untrusted strings into the command?
   - Any in PATH-dependent contexts (assuming `git`, `sqlite3`, etc., are
     present and trustworthy)?

5. **Secrets handling.** The auth path uses CLAUDE_CODE_OAUTH_TOKEN via
   `claude setup-token`. Audit:
   - Token logging (is it ever printed, logged, written to a JSONL file?)
   - Token in error messages or stack traces
   - Token in analytics events or thread entries
   - Other secrets (Postgres credentials in connection-string templates?)
   - .env file handling — is anything written to .env files automatically?

6. **Git operations.** `git commit`, `git apply`, `git diff` paths in the
   reviewer auto-apply, the worktree manager, and the per-commit hook
   runner. Audit:
   - Can a malicious patch escape the worktree via `git apply`?
   - Can untrusted branch names be used in shell commands?
   - Is GPG signing properly handled (or properly disabled in the container)?

7. **Hook installation.** `jig/hooks/per_commit.py` writes scripts into
   `.git/hooks/`. Audit:
   - The script content — is operator-supplied data ever interpolated?
   - Permissions on the written file
   - The runner's invocation path

## How to run things

- `uv run pytest tests/` to verify any change you suggest doesn't break tests
- Read the safe_path primitive first: `jig/safe_path.py` and
  `tests/test_safe_path.py`. Don't re-flag what it already covers.

## Output format

Markdown, three sections:

### Critical findings
Real exploitable issues, agent-to-host escapes, secrets exposure. Each:
file:line, the vector, exploitability, suggested fix.

### Important findings
Defense-in-depth gaps, validation that should exist but doesn't, places where
a hostile prompt could cause damage in a multi-tenant scenario.

### Notable observations
Patterns to watch (e.g., places where adding a malicious role config could
cause issues, areas where the threat model assumes things that might not
hold).

## What NOT to focus on

- Style nitpicks
- Block 1's path-traversal work (already done; verify it but don't re-flag)
- Things explicitly out-of-scope per docs/v2.0/implementation/v2-plan.md
  (deferred to v2.x)

## Discipline

If you find a serious issue, file:line + suggested fix is enough. Don't
write a redesign — the review is checking what landed.

End with one sentence: "v2 is safe to ship / v2 has an exploitable issue at X"
+ the single biggest concern.
