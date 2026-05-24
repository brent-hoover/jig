---
name: jig-mcp-tools
description: "Reference for all Jig MCP tools in your session: create_ticket, comment_on_ticket, update_ticket, commit_progress, and more"
applies_to: {}
---

# Jig MCP tools

You have a `jig` MCP server. Use it for all work-tracking actions.

## Tools

- `create_ticket(type, title, description, assignee?, parent_id?)` — file a new ticket. Use type=question with assignee="user" to ask the user a clarifying question.
- `comment_on_ticket(ticket_id, content)` — post a comment. Use this to narrate progress, explain decisions, or reply to another agent.
- `update_ticket(ticket_id, status=...)` — transition ticket status. Use status=resolved when work is complete, blocked if waiting on external input, needs_info if waiting on a question.
- `commit_progress(ticket_id, message)` — git commit in the ticket worktree, file a commit comment, publish to the bus. Call this after every meaningful chunk of work.
- `record_learning(content)` — save a role-wide lesson you want to remember in future spawns.

## Rules

- Always `commit_progress` after a chunk of work lands. Small, frequent commits are preferred over one large one.
- When you're done, call `update_ticket(status="resolved")` on your primary ticket.
- To ask another role a question, use `create_ticket(type="question", assignee="<role>", parent_id=<current_ticket>, description="...")` and wait for the answer to arrive as a conversation turn.
