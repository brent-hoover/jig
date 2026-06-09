"""Issue tracker front doors over jig's per-project store.

``IssueService`` is the context-free seam (no worktree, agent identity, or bus)
shared by the ``jig issue`` CLI and the standalone stdio MCP server.
"""

from jig.issues.service import IssueService

__all__ = ["IssueService"]
