"""Build and materialise the jig dependency graph (Phase 3.7).

Three public functions:

- ``build_graph(project_root)`` — reads .jig/spec/ + .jig/store/tickets.jsonl
  and returns a ``DependencyGraph``. Pure in the side-effect sense: reads
  files, returns a value, never writes.

- ``write_graph(project_root)`` — calls ``build_graph`` then atomically
  writes ``.jig/graph/dependency-graph.yaml``. Returns the output path.

- ``ticket_impact(graph, ticket_id, *, depth)`` — projects the graph around
  the ticket's touches. Pure: no I/O, no mutation.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import yaml

from jig.graph.types import DependencyGraph, Edge, Node, TicketImpact


def build_graph(project_root: Path) -> DependencyGraph:
    """Materialise the dependency graph from authored spec artifacts.

    Sources:
    - ``.jig/spec/architecture.yaml`` (always required; returns empty
      graph when absent)
    - ``.jig/spec/modules/<id>/contracts.yaml`` per module (best-effort)
    - ``.jig/spec/suites.yaml`` (optional)
    - ``.jig/store/tickets.jsonl`` (optional)
    """
    arch_path = project_root / ".jig" / "spec" / "architecture.yaml"
    if not arch_path.exists():
        return DependencyGraph(generated_at=datetime.now(timezone.utc))

    from jig.spec_loader import load_architecture, load_module_contracts

    try:
        arch = load_architecture(project_root)
    except Exception:
        return DependencyGraph(generated_at=datetime.now(timezone.utc))

    nodes: list[Node] = []
    edges: list[Edge] = []
    node_ids: set[str] = set()

    def _add_node(node: Node) -> None:
        if node.id not in node_ids:
            node_ids.add(node.id)
            nodes.append(node)

    def _add_edge(edge: Edge) -> None:
        edges.append(edge)

    # Data stores
    for ds in arch.data_stores:
        _add_node(
            Node(
                id=f"data_store:{ds.id}",
                kind="data_store",
                title=ds.id,
                uri=f"project://spec/architecture#data_stores/{ds.id}",
            )
        )

    # Modules + per-module contracts
    for module in arch.modules:
        mod_nid = f"module:{module.id}"
        _add_node(
            Node(
                id=mod_nid,
                kind="module",
                title=module.title,
                uri=f"project://spec/modules/{module.id}",
            )
        )

        for cap_id in module.implements_capabilities:
            cap_nid = f"capability:{cap_id}"
            _add_node(Node(id=cap_nid, kind="capability", title=cap_id))
            _add_edge(Edge(src=mod_nid, dst=cap_nid, kind="implements"))

        # Consumption edges declared on the module
        for api_cons in module.consumes_apis:
            api_nid = f"exposed_api:{api_cons.module}:{api_cons.name}"
            _add_node(
                Node(
                    id=api_nid,
                    kind="exposed_api",
                    title=api_cons.name,
                    uri=(
                        f"project://spec/modules/{api_cons.module}"
                        f"/contracts#exposes/{api_cons.name}"
                    ),
                )
            )
            _add_edge(Edge(src=mod_nid, dst=api_nid, kind="consumes"))

        for ev_cons in module.consumes_events:
            ev_nid = f"emitted_event:{ev_cons.module}:{ev_cons.name}"
            _add_node(
                Node(
                    id=ev_nid,
                    kind="emitted_event",
                    title=ev_cons.name,
                    uri=(
                        f"project://spec/modules/{ev_cons.module}"
                        f"/contracts#emits/{ev_cons.name}"
                    ),
                )
            )
            _add_edge(Edge(src=mod_nid, dst=ev_nid, kind="consumes"))

        # Per-module contracts file
        try:
            cf = load_module_contracts(project_root, module.id)
        except Exception:
            continue

        for api in cf.exposes:
            api_nid = f"exposed_api:{module.id}:{api.name}"
            _add_node(
                Node(
                    id=api_nid,
                    kind="exposed_api",
                    title=api.name,
                    uri=(
                        f"project://spec/modules/{module.id}"
                        f"/contracts#exposes/{api.name}"
                    ),
                )
            )
            _add_edge(Edge(src=mod_nid, dst=api_nid, kind="exposes"))

        for ev in cf.emits:
            ev_nid = f"emitted_event:{module.id}:{ev.name}"
            _add_node(
                Node(
                    id=ev_nid,
                    kind="emitted_event",
                    title=ev.name,
                    uri=(
                        f"project://spec/modules/{module.id}/contracts#emits/{ev.name}"
                    ),
                )
            )
            _add_edge(Edge(src=mod_nid, dst=ev_nid, kind="emits"))

        for owned in cf.owns:
            coll_nid = f"owned_collection:{module.id}:{owned.collection}"
            _add_node(
                Node(
                    id=coll_nid,
                    kind="owned_collection",
                    title=owned.collection,
                    uri=(
                        f"project://spec/modules/{module.id}"
                        f"/contracts#owns/{owned.collection}"
                    ),
                )
            )
            _add_edge(Edge(src=mod_nid, dst=coll_nid, kind="owns"))
            ds_nid = f"data_store:{owned.db}"
            _add_edge(Edge(src=coll_nid, dst=ds_nid, kind="backed_by"))

        for dep in cf.external_dependencies:
            dep_nid = f"external_dependency:{module.id}:{dep.id}"
            _add_node(
                Node(
                    id=dep_nid,
                    kind="external_dependency",
                    title=dep.id,
                    uri=(
                        f"project://spec/modules/{module.id}"
                        f"/contracts#external_dependencies/{dep.id}"
                    ),
                )
            )
            _add_edge(Edge(src=mod_nid, dst=dep_nid, kind="uses"))

        for bc in cf.behavioral_contracts:
            bc_nid = f"behavioral_contract:{module.id}:{bc.id}"
            _add_node(
                Node(
                    id=bc_nid,
                    kind="behavioral_contract",
                    title=bc.id,
                    uri=(
                        f"project://spec/modules/{module.id}"
                        f"/contracts#behavioral_contracts/{bc.id}"
                    ),
                )
            )
            _add_edge(Edge(src=mod_nid, dst=bc_nid, kind="exposes"))

        for dc in cf.data_contracts:
            dc_nid = f"data_contract:{module.id}:{dc.id}"
            _add_node(
                Node(
                    id=dc_nid,
                    kind="data_contract",
                    title=dc.id,
                    uri=(
                        f"project://spec/modules/{module.id}"
                        f"/contracts#data_contracts/{dc.id}"
                    ),
                )
            )
            _add_edge(Edge(src=mod_nid, dst=dc_nid, kind="exposes"))

    # Architecture-level risks
    for risk in arch.risks:
        _add_node(
            Node(
                id=f"risk:{risk.id}",
                kind="risk",
                title=risk.id,
                uri=f"project://spec/architecture#risks/{risk.id}",
            )
        )

    # Suites (capabilities + epic-level grouping)
    suites_path = project_root / ".jig" / "spec" / "suites.yaml"
    if suites_path.exists():
        try:
            from jig.spec_loader import load_suites_index

            suites = load_suites_index(project_root)
            for suite in suites.suites:
                suite_nid = f"epic:{suite.id}"
                _add_node(
                    Node(
                        id=suite_nid,
                        kind="epic",
                        title=suite.title,
                        uri=f"project://spec/suites/{suite.id}",
                    )
                )
                for cap_id in suite.capabilities:
                    cap_nid = f"capability:{cap_id}"
                    _add_node(Node(id=cap_nid, kind="capability", title=cap_id))
                    _add_edge(Edge(src=cap_nid, dst=suite_nid, kind="covers"))
        except Exception:
            pass

    # Tracers (Phase 5.12)
    try:
        from jig.spec_loader import load_all_tracers

        for tr in load_all_tracers(project_root):
            tr_nid = f"tracer:{tr.id}"
            _add_node(
                Node(
                    id=tr_nid,
                    kind="tracer",
                    title=tr.description,
                    uri=f"project://spec/tracers/{tr.id}",
                )
            )
            for mod_id in tr.covers.modules:
                _add_edge(Edge(src=tr_nid, dst=f"module:{mod_id}", kind="covers"))
            for cap_id in tr.covers.capabilities:
                _add_edge(Edge(src=tr_nid, dst=f"capability:{cap_id}", kind="covers"))
            for api_ref in tr.covers.exposed_apis:
                parts = api_ref.split(":", 1)
                if len(parts) == 2:
                    _add_edge(
                        Edge(
                            src=tr_nid,
                            dst=f"exposed_api:{parts[0]}:{parts[1]}",
                            kind="covers",
                        )
                    )
            for ev_ref in tr.covers.emitted_events:
                parts = ev_ref.split(":", 1)
                if len(parts) == 2:
                    _add_edge(
                        Edge(
                            src=tr_nid,
                            dst=f"emitted_event:{parts[0]}:{parts[1]}",
                            kind="covers",
                        )
                    )
    except Exception:
        pass

    # Tickets from the JSONL store
    tickets_path = project_root / ".jig" / "store" / "tickets.jsonl"
    if tickets_path.exists():
        for line in tickets_path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                _build_ticket_nodes(line, node_ids, nodes, edges)
            except Exception:
                continue

    return DependencyGraph(
        generated_at=datetime.now(timezone.utc),
        nodes=nodes,
        edges=edges,
    )


def _build_ticket_nodes(
    jsonl_line: str,
    node_ids: set[str],
    nodes: list[Node],
    edges: list[Edge],
) -> None:
    """Parse one JSONL line and add ticket node + touches edges."""
    raw = json.loads(jsonl_line)
    ticket_id = raw.get("id") or raw.get("_id")
    if not ticket_id:
        return
    t_nid = f"ticket:{ticket_id}"
    if t_nid not in node_ids:
        node_ids.add(t_nid)
        nodes.append(
            Node(
                id=t_nid,
                kind="ticket",
                title=raw.get("title"),
                uri=f"project://tickets/{ticket_id}",
            )
        )
    if raw.get("module_id"):
        edges.append(Edge(src=t_nid, dst=f"module:{raw['module_id']}", kind="touches"))
    for cap_id in raw.get("capability_ids", []):
        edges.append(Edge(src=t_nid, dst=f"capability:{cap_id}", kind="touches"))
    touches = raw.get("touches") or {}
    for m in touches.get("modules", []):
        edges.append(Edge(src=t_nid, dst=f"module:{m}", kind="touches"))
    for api in touches.get("exposed_apis", []):
        parts = api.split(":", 1)
        if len(parts) == 2:
            edges.append(
                Edge(
                    src=t_nid, dst=f"exposed_api:{parts[0]}:{parts[1]}", kind="touches"
                )
            )
    for ev in touches.get("emitted_events", []):
        parts = ev.split(":", 1)
        if len(parts) == 2:
            edges.append(
                Edge(
                    src=t_nid,
                    dst=f"emitted_event:{parts[0]}:{parts[1]}",
                    kind="touches",
                )
            )
    for ds in touches.get("data_stores", []):
        edges.append(Edge(src=t_nid, dst=f"data_store:{ds}", kind="touches"))
    for bc in touches.get("behavioral_contracts", []):
        parts = bc.split(":", 1)
        if len(parts) == 2:
            edges.append(
                Edge(
                    src=t_nid,
                    dst=f"behavioral_contract:{parts[0]}:{parts[1]}",
                    kind="touches",
                )
            )
    for dc in touches.get("data_contracts", []):
        parts = dc.split(":", 1)
        if len(parts) == 2:
            edges.append(
                Edge(
                    src=t_nid,
                    dst=f"data_contract:{parts[0]}:{parts[1]}",
                    kind="touches",
                )
            )


def write_graph(project_root: Path) -> Path:
    """Build the graph and atomically write ``.jig/graph/dependency-graph.yaml``."""
    from jig.atomic import atomic_write_text

    graph = build_graph(project_root)
    out_path = project_root / ".jig" / "graph" / "dependency-graph.yaml"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        out_path,
        yaml.safe_dump(graph.model_dump(mode="json"), sort_keys=False),
    )
    return out_path


def ticket_impact(
    graph: DependencyGraph,
    ticket_id: str,
    *,
    depth: int = 1,
) -> TicketImpact:
    """Project the graph around a ticket's touches. Pure; no I/O.

    ``depth`` controls how far outward from directly touched nodes to
    walk when computing ``consumers`` and ``exercised_tracers``. At
    depth=1 (default), only the direct neighbors of touched nodes are
    considered. ``crossed_boundaries`` counts unique module nodes in
    the walked subgraph minus one (zero for single-module changes).
    """
    t_nid = f"ticket:{ticket_id}"
    node_map = {n.id: n for n in graph.nodes}

    # Directly touched nodes (ticket → touches → X)
    directly_touched: set[str] = {
        e.dst for e in graph.edges if e.src == t_nid and e.kind == "touches"
    }

    # Depth-limited neighborhood from touched set
    all_walked: set[str] = set(directly_touched)
    frontier = set(directly_touched)
    for _ in range(depth):
        next_frontier: set[str] = set()
        for nid in frontier:
            for nbr in graph.neighbors(nid, depth=1):
                if nbr not in all_walked:
                    all_walked.add(nbr)
                    next_frontier.add(nbr)
        frontier = next_frontier

    touched_nodes = [node_map[nid] for nid in directly_touched if nid in node_map]

    # Consumers: for each directly touched node, who else points at it?
    consumers: dict[str, list[Node]] = {}
    for nid in directly_touched:
        incoming = [
            node_map[e.src]
            for e in graph.edges
            if e.dst == nid and e.src != t_nid and e.src in node_map
        ]
        if incoming:
            consumers[nid] = incoming

    # Exercised tracers: tracers whose "covers" edge points at a directly
    # touched node. Tracers point TO modules (tracer → module), so we
    # check incoming "covers" edges on each touched node.
    seen_tracers: set[str] = set()
    exercised_tracers: list[Node] = []
    for nid in directly_touched:
        for e in graph.edges:
            if e.dst == nid and e.kind == "covers" and e.src not in seen_tracers:
                tr_node = node_map.get(e.src)
                if tr_node is not None and tr_node.kind == "tracer":
                    seen_tracers.add(e.src)
                    exercised_tracers.append(tr_node)

    modules_in_walk = {
        nid for nid in all_walked if nid in node_map and node_map[nid].kind == "module"
    }
    crossed_boundaries = max(0, len(modules_in_walk) - 1)

    return TicketImpact(
        ticket_id=ticket_id,
        generated_at=datetime.now(timezone.utc),
        touched=touched_nodes,
        consumers=consumers,
        exercised_tracers=exercised_tracers,
        crossed_boundaries=crossed_boundaries,
    )


__all__ = ["build_graph", "ticket_impact", "write_graph"]
