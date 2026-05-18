"""Graph-narrowed context loader for agent spawns (Phase 4.9).

When ``JIG_GRAPH_CONTEXT=1`` is set, agents receive the contracts for
modules in their ticket's graph neighborhood instead of loading every
related artifact blindly. Behind a feature flag for before/after eval
comparison.

Usage in agent.py::

    if os.environ.get("JIG_GRAPH_CONTEXT"):
        graph_ctx = await build_graph_context(ticket, project_path, depth=1)
    else:
        graph_ctx = ""
"""

from __future__ import annotations

from pathlib import Path

from jig.ticket import Ticket


async def build_graph_context(
    ticket: Ticket,
    project_path: Path,
    *,
    depth: int = 1,
) -> str:
    """Return spec artifact text for the ticket's graph neighborhood.

    Loads ``contracts.yaml`` for each module node in the ``depth``-hop
    neighborhood of the ticket's touched nodes. Returns ``""`` when the
    architecture doesn't exist or the ticket has no module touches.

    The returned text is ready to be appended to ``resolved_context``
    in the spawn prompt; it includes a ``## Graph Context`` header.
    """
    from jig.graph.derive import build_graph, ticket_impact

    try:
        graph = build_graph(project_path)
    except Exception:
        return ""

    impact = ticket_impact(graph, ticket.id, depth=depth)

    # Collect module nodes: directly touched + depth-hop neighbors
    all_walked: set[str] = set()
    for node in impact.touched:
        all_walked.add(node.id)
        for nbr in graph.neighbors(node.id, depth=depth):
            all_walked.add(nbr)

    node_map = {n.id: n for n in graph.nodes}
    module_ids = [
        nid.split(":", 1)[1]
        for nid in sorted(all_walked)
        if nid.startswith("module:") and nid in node_map
    ]

    if not module_ids:
        return ""

    sections: list[str] = []
    for module_id in module_ids:
        text = _load_module_contracts_text(project_path, module_id)
        if text:
            sections.append(
                f"### contracts: {module_id}\n\n```yaml\n{text.strip()}\n```"
            )

    if not sections:
        return ""

    return "## Graph Context\n\n" + "\n\n".join(sections) + "\n\n"


def _load_module_contracts_text(project_path: Path, module_id: str) -> str:
    """Return the raw text of a module's contracts.yaml, or '' if absent."""
    contracts_path = (
        project_path / ".jig" / "spec" / "modules" / module_id / "contracts.yaml"
    )
    if not contracts_path.is_file():
        return ""
    return contracts_path.read_text()


__all__ = ["build_graph_context"]
