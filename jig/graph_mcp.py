"""MCP tool handlers for the dependency graph (Phase 3.8).

Five handlers wired into create_agent_mcp_server:

- graph_get_impact   — TicketImpact for a ticket
- graph_neighbors    — depth-limited outgoing neighbors of a node
- graph_consumers_of — nodes with edges pointing TO a node
- graph_tracers_for  — tracer nodes reachable from a node
- graph_changed_interfaces — touched nodes that are public interface kinds
                             (exposed_api, emitted_event, data_contract)
"""
from __future__ import annotations

from pathlib import Path

from jig.graph.derive import build_graph, ticket_impact
from jig.graph.types import DependencyGraph, Node


def _load_graph(project_root: Path) -> DependencyGraph:
    """Build the graph fresh from spec artifacts each call.

    Rebuilding is cheap (seconds) for MVP project sizes and avoids
    stale-snapshot issues. A caching layer can land once the call
    frequency justifies it.
    """
    return build_graph(project_root)


# ---- interface kinds that count as "changed interface" ---------------------

_INTERFACE_KINDS: frozenset[str] = frozenset({
    "exposed_api",
    "emitted_event",
    "data_contract",
})


# ---- handlers ---------------------------------------------------------------


async def handle_graph_get_impact(
    project_root: Path,
    ticket_id: str,
    *,
    depth: int = 1,
) -> dict:
    """Return the TicketImpact for ``ticket_id`` as a JSON-serialisable dict."""
    graph = _load_graph(project_root)
    impact = ticket_impact(graph, ticket_id, depth=depth)
    return impact.model_dump(mode="json")


async def handle_graph_neighbors(
    project_root: Path,
    node_id: str,
    *,
    depth: int = 1,
    kind: str | None = None,
) -> list[dict]:
    """Return the neighboring nodes of ``node_id`` within ``depth`` hops."""
    graph = _load_graph(project_root)
    neighbor_ids = graph.neighbors(node_id, depth=depth, kind=kind)
    node_map = {n.id: n for n in graph.nodes}
    result: list[Node] = [node_map[nid] for nid in neighbor_ids if nid in node_map]
    result.sort(key=lambda n: (n.kind, n.id))
    return [n.model_dump(mode="json") for n in result]


async def handle_graph_consumers_of(
    project_root: Path,
    node_id: str,
) -> list[dict]:
    """Return all nodes that have an outgoing edge pointing TO ``node_id``."""
    graph = _load_graph(project_root)
    consumer_ids = graph.consumers_of(node_id)
    node_map = {n.id: n for n in graph.nodes}
    result: list[Node] = [node_map[nid] for nid in consumer_ids if nid in node_map]
    result.sort(key=lambda n: (n.kind, n.id))
    return [n.model_dump(mode="json") for n in result]


async def handle_graph_tracers_for(
    project_root: Path,
    node_id: str,
) -> list[dict]:
    """Return tracer nodes reachable from ``node_id`` in the graph.

    Phase 5 will populate tracer nodes from ``.jig/spec/tracers/``. For
    now, returns any nodes with ``kind == "tracer"`` reachable from the
    given node — typically empty until Phase 5 lands.
    """
    graph = _load_graph(project_root)
    reachable = graph.reachable_from(node_id)
    node_map = {n.id: n for n in graph.nodes}
    tracers = [
        node_map[nid]
        for nid in reachable
        if nid in node_map and node_map[nid].kind == "tracer"
    ]
    tracers.sort(key=lambda n: n.id)
    return [t.model_dump(mode="json") for t in tracers]


async def handle_graph_changed_interfaces(
    project_root: Path,
    ticket_id: str,
) -> list[dict]:
    """Return the public interface nodes touched by ``ticket_id``.

    Public interface kinds: ``exposed_api``, ``emitted_event``,
    ``data_contract``. The "vs. last green tracer" diff is Phase 5
    scope; for MVP this returns all touched interface nodes so the
    Coordinator / PM can see what surfaces this ticket modifies.
    """
    graph = _load_graph(project_root)
    t_nid = f"ticket:{ticket_id}"
    touched_ids = {
        e.dst for e in graph.edges
        if e.src == t_nid and e.kind == "touches"
    }
    node_map = {n.id: n for n in graph.nodes}
    interface_nodes = [
        node_map[nid]
        for nid in touched_ids
        if nid in node_map and node_map[nid].kind in _INTERFACE_KINDS
    ]
    interface_nodes.sort(key=lambda n: (n.kind, n.id))
    return [n.model_dump(mode="json") for n in interface_nodes]


__all__ = [
    "handle_graph_changed_interfaces",
    "handle_graph_consumers_of",
    "handle_graph_get_impact",
    "handle_graph_neighbors",
    "handle_graph_tracers_for",
]
