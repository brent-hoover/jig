---
name: block-writes-outside-worktree
enabled: true
event: file
tool_matcher: Edit|Write|MultiEdit
conditions:
  - field: file_path
    operator: starts_with
    pattern: /
  - field: file_path
    operator: not_contains
    pattern: .worktrees/
  - field: file_path
    operator: not_contains
    pattern: .claude/
  - field: file_path
    operator: not_contains
    pattern: .remember/
action: block
---

**BLOCKED: File write to main checkout (develop)**

You are attempting to write a file outside of a worktree. All feature work — code,
docs, plans — must go in a worktree branch, not the main checkout.

**What to do:**
- If a worktree already exists for this work, write the file there instead
- If no worktree exists yet, create one first: `git worktree add .worktrees/<branch> -b <branch>`
- Check existing worktrees: `git worktree list`

Writing to develop directly (even without committing) pollutes the main branch
and makes cleanup difficult.

Note: this rule is scoped to write tools (`tool_matcher: Edit|Write|MultiEdit`) so that
read-only tools (Read) are never blocked by it.
