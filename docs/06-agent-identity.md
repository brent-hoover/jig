# 06 — Agent Identity and Templates

## Two-layer identity

**Template identity.** Static, version-controlled, defines capabilities and
defaults. "The dev role." "The reviewer role." What a human references when
they say "spawn a dev agent."

**Instance identity.** Ephemeral, per-spawn, tied to a specific work unit.
"The dev-agent-instance-4f2a that worked on work-unit-123, spawned by
dev@team at T." What shows up in audit logs and threads.

Why the split:

- Policy can attach to either layer. "All dev-role agents have these tools"
  (template) vs "this specific instance has elevated scope because the
  human approved it for this task" (instance).
- Threads attach to instances, not templates. You want to see what *this
  particular reviewer run* said, not all reviewer runs ever.
- Audit wants instance resolution; configuration wants template resolution.

## Template declaration — what it expresses

The current YAML has `role`, `system_prompt`, `allowed_tools`,
`default_context`. That's a good start. The enriched shape should express:

- **Identity**: template id, version, display name.
- **Prompt composition**: fragments this role contributes + fragments it
  expects to inherit. A dev agent's actual system prompt is roughly
  `[base instructions] + [role-specific dev] + [project conventions] +
  [work-unit context]`. Inlining loses reuse and project injection.
- **Capabilities**: tools, tool-parameter constraints, path constraints —
  expressed as *intent*. The harness translates intent to concrete Claude
  Code hook configuration at spawn time.
- **Context requirements**: required and optional context bundle
  references (`issue://design`, `project://conventions`, etc.). Required
  resolution failures should fail the spawn.
- **Inputs**: what the role expects from upstream phase output.
- **Outputs**: what the role produces (diff, review verdict, plan,
  decision record). Declared shape.
- **Exit criteria**: machine-checkable conditions for "ready for handoff."
  Problem 7 fix: not self-certified past these gates.
- **Resource limits**: max duration, max tokens, max tool calls. Enforced
  by the service.
- **Escalation paths**: when and how to ask for help. Which role, specific
  human, any human. The mechanism that makes mid-flight human intervention
  (problem 2) clean.

## Capability expression

Three grains, all needed:

- **Tool-level**: can this role use Bash at all? (What the current YAML has.)
- **Tool-parameter**: Bash yes, but not `rm -rf`, not `curl` to arbitrary
  hosts, not `git push`. This is where real safety lives.
- **Path**: Write yes, but only within certain directories. A reviewer
  shouldn't write to `src/`; a dev shouldn't write to `.jig/decisions/`.

The YAML declares intent; the harness produces concrete hook configuration
at spawn time. Template stays readable, enforcement is real.

## Context resolution

URI-style references (`workunit://design`, `project://conventions`)
resolved by the service. Open question: what's the full scheme, and how
does versioning work for long-running agents when project context
changes mid-flight?

Working answer for now: the agent has what it had at spawn; context is
snapshotted to the instance. If a project convention changes, the next
spawn gets the new version. See [99 — Open questions](./99-open-questions.md).

## Identifier note

`role: dev` works for now, but you'll end up with variants (frontend-dev,
backend-dev, infra-dev) with different scopes. A template-id plus a role
field scales better than overloading the role field.

## Current YAML for reference

```yaml
role: dev
system_prompt: >
  You are a development agent. Your job is to implement code changes based
  on the design doc and implementation plan. Write clean, well-structured code
  that passes the existing tests. Use the report_completion tool when finished.
allowed_tools:
  - Read
  - Edit
  - Write
  - Glob
  - Grep
  - Bash
default_context:
  - "workunit://design"
  - "workunit://plan"
```

The shape it needs to grow into isn't fully specified yet — that's partly a
workflow question (see [05](./05-workflow-model.md)) and partly a context
bundle question (open).
