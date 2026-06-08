---
name: block-writes-to-agent-worktrees
enabled: true
event: file
conditions:
  - field: file_path
    operator: contains
    pattern: /.claude/worktrees/
action: block
---

**BLOCKED: File write to .claude/worktrees/**

`.claude/worktrees/` is managed by jig's internal orchestrator for agent sandboxes.
You must not write files there directly.

If you need a worktree for feature development, use `.worktrees/` instead:
- Check existing worktrees: `git worktree list`
- Create one: `git worktree add .worktrees/<branch> -b <branch>`
