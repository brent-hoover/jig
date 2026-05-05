"""Phase 3.7 — DependencyGraph types, build_graph, ticket_impact tests.

Covers:
  - Node / Edge / DependencyGraph / TicketImpact construction
  - DependencyGraph.neighbors (depth 1 and 2, kind filter, cycle safety)
  - DependencyGraph.consumers_of
  - DependencyGraph.reachable_from
  - build_graph: empty when no architecture.yaml
  - build_graph: module, data_store, exposed_api, emitted_event nodes
  - build_graph: consumes edges from Module.consumes_apis / consumes_events
  - build_graph: owned_collection + backed_by data_store
  - build_graph: ticket nodes + touches edges from JSONL store
  - write_graph: materialises .jig/graph/dependency-graph.yaml
  - ticket_impact: touched, consumers, crossed_boundaries
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import yaml


# ---- helpers ----------------------------------------------------------------


def _ts() -> datetime:
    return datetime.now(timezone.utc)


def _minimal_intent() -> dict:
    return {
        "problem": "Need to do X.",
        "simplest_solution": "One function.",
        "complications_considered": {},
    }


def _write_arch(root: Path, modules: list[dict], data_stores=None) -> None:
    p = root / ".jig" / "spec" / "architecture.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.dump({
        "spec_version": 1,
        "data_stores": data_stores or [],
        "modules": modules,
    }, allow_unicode=True))


def _write_contracts(root: Path, module_id: str, data: dict) -> None:
    p = root / ".jig" / "spec" / "modules" / module_id / "contracts.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.dump({"spec_version": 1, "module": module_id, **data}, allow_unicode=True))


def _minimal_module(mid: str, **extra) -> dict:
    return {
        "id": mid,
        "title": mid.title(),
        "summary": f"Module {mid}.",
        "intent": _minimal_intent(),
        "n_a_categories": ["behavioral_contracts", "external_dependencies", "ownership"],
        **extra,
    }


# ---- types ------------------------------------------------------------------


def test_node_construction():
    from jig.graph.types import Node
    n = Node(id="module:foo", kind="module", title="Foo")
    assert n.id == "module:foo"
    assert n.kind == "module"


def test_edge_construction():
    from jig.graph.types import Edge
    e = Edge(src="module:a", dst="module:b", kind="consumes")
    assert e.kind == "consumes"


def test_dependency_graph_defaults():
    from jig.graph.types import DependencyGraph
    g = DependencyGraph(generated_at=_ts())
    assert g.nodes == []
    assert g.edges == []
    assert g.spec_version == 1


def test_ticket_impact_defaults():
    from jig.graph.types import TicketImpact
    ti = TicketImpact(ticket_id="t-01", generated_at=_ts())
    assert ti.touched == []
    assert ti.consumers == {}
    assert ti.exercised_tracers == []
    assert ti.crossed_boundaries == 0


# ---- graph traversal --------------------------------------------------------


def _simple_graph():
    from jig.graph.types import DependencyGraph, Edge, Node
    nodes = [
        Node(id="module:a", kind="module"),
        Node(id="module:b", kind="module"),
        Node(id="module:c", kind="module"),
        Node(id="exposed_api:a:fn", kind="exposed_api"),
    ]
    edges = [
        Edge(src="module:a", dst="module:b", kind="consumes"),
        Edge(src="module:b", dst="module:c", kind="consumes"),
        Edge(src="module:a", dst="exposed_api:a:fn", kind="exposes"),
    ]
    return DependencyGraph(generated_at=_ts(), nodes=nodes, edges=edges)


def test_neighbors_depth_1():
    g = _simple_graph()
    nbrs = g.neighbors("module:a", depth=1)
    assert nbrs == {"module:b", "exposed_api:a:fn"}


def test_neighbors_depth_2():
    g = _simple_graph()
    nbrs = g.neighbors("module:a", depth=2)
    assert "module:b" in nbrs
    assert "module:c" in nbrs


def test_neighbors_kind_filter():
    g = _simple_graph()
    nbrs = g.neighbors("module:a", depth=2, kind="module")
    assert nbrs == {"module:b", "module:c"}
    assert "exposed_api:a:fn" not in nbrs


def test_neighbors_depth_zero():
    g = _simple_graph()
    assert g.neighbors("module:a", depth=0) == set()


def test_neighbors_cycle_safe():
    from jig.graph.types import DependencyGraph, Edge, Node
    # A → B → A cycle
    g = DependencyGraph(
        generated_at=_ts(),
        nodes=[Node(id="module:a", kind="module"), Node(id="module:b", kind="module")],
        edges=[
            Edge(src="module:a", dst="module:b", kind="consumes"),
            Edge(src="module:b", dst="module:a", kind="consumes"),
        ],
    )
    nbrs = g.neighbors("module:a", depth=3)
    assert nbrs == {"module:b"}  # cycle doesn't cause infinite loop


def test_consumers_of():
    g = _simple_graph()
    consumers = g.consumers_of("module:b")
    assert consumers == {"module:a"}


def test_reachable_from():
    g = _simple_graph()
    reachable = g.reachable_from("module:a")
    assert "module:b" in reachable
    assert "module:c" in reachable
    assert "exposed_api:a:fn" in reachable


def test_reachable_from_cycle_safe():
    from jig.graph.types import DependencyGraph, Edge, Node
    g = DependencyGraph(
        generated_at=_ts(),
        nodes=[Node(id="module:a", kind="module"), Node(id="module:b", kind="module")],
        edges=[
            Edge(src="module:a", dst="module:b", kind="consumes"),
            Edge(src="module:b", dst="module:a", kind="consumes"),
        ],
    )
    assert g.reachable_from("module:a") == {"module:b"}


# ---- build_graph ------------------------------------------------------------


def test_build_graph_no_arch_returns_empty(tmp_path):
    from jig.graph.derive import build_graph
    g = build_graph(tmp_path)
    assert g.nodes == []
    assert g.edges == []


def test_build_graph_module_and_data_store(tmp_path):
    from jig.graph.derive import build_graph
    _write_arch(tmp_path, [_minimal_module("api-client")], data_stores=[{"id": "main-db", "kind": "postgres"}])
    g = build_graph(tmp_path)
    node_ids = {n.id for n in g.nodes}
    assert "module:api-client" in node_ids
    assert "data_store:main-db" in node_ids


def test_build_graph_exposed_api_node_and_edge(tmp_path):
    from jig.graph.derive import build_graph
    _write_arch(tmp_path, [_minimal_module("provider")])
    _write_contracts(tmp_path, "provider", {
        "owns": [],
        "exposes": [{"name": "get_data", "kind": "function", "summary": "Get data."}],
        "emits": [],
        "integration_ac": [],
    })
    g = build_graph(tmp_path)
    node_ids = {n.id for n in g.nodes}
    edge_pairs = {(e.src, e.dst, e.kind) for e in g.edges}
    assert "exposed_api:provider:get_data" in node_ids
    assert ("module:provider", "exposed_api:provider:get_data", "exposes") in edge_pairs


def test_build_graph_emitted_event(tmp_path):
    from jig.graph.derive import build_graph
    _write_arch(tmp_path, [_minimal_module("emitter")])
    _write_contracts(tmp_path, "emitter", {
        "owns": [],
        "exposes": [],
        "emits": [{"name": "data.created", "summary": "Created."}],
        "integration_ac": [],
    })
    g = build_graph(tmp_path)
    node_ids = {n.id for n in g.nodes}
    assert "emitted_event:emitter:data.created" in node_ids
    edges = {(e.src, e.dst, e.kind) for e in g.edges}
    assert ("module:emitter", "emitted_event:emitter:data.created", "emits") in edges


def test_build_graph_consumes_api_edge(tmp_path):
    from jig.graph.derive import build_graph
    _write_arch(tmp_path, [
        _minimal_module("provider"),
        _minimal_module("consumer", consumes_apis=[{"module": "provider", "name": "get_data"}]),
    ])
    g = build_graph(tmp_path)
    edges = {(e.src, e.dst, e.kind) for e in g.edges}
    assert ("module:consumer", "exposed_api:provider:get_data", "consumes") in edges


def test_build_graph_consumes_event_edge(tmp_path):
    from jig.graph.derive import build_graph
    _write_arch(tmp_path, [
        _minimal_module("publisher"),
        _minimal_module("subscriber", consumes_events=[{"module": "publisher", "name": "evt.fired"}]),
    ])
    g = build_graph(tmp_path)
    edges = {(e.src, e.dst, e.kind) for e in g.edges}
    assert ("module:subscriber", "emitted_event:publisher:evt.fired", "consumes") in edges


def test_build_graph_owned_collection_backed_by_store(tmp_path):
    from jig.graph.derive import build_graph
    _write_arch(
        tmp_path,
        [_minimal_module("store-mod")],
        data_stores=[{"id": "main-db", "kind": "postgres"}],
    )
    _write_contracts(tmp_path, "store-mod", {
        "owns": [{"collection": "items", "db": "main-db", "write_access": ["store-mod"]}],
        "exposes": [],
        "emits": [],
        "integration_ac": [],
    })
    g = build_graph(tmp_path)
    node_ids = {n.id for n in g.nodes}
    edges = {(e.src, e.dst, e.kind) for e in g.edges}
    assert "owned_collection:store-mod:items" in node_ids
    assert ("module:store-mod", "owned_collection:store-mod:items", "owns") in edges
    assert ("owned_collection:store-mod:items", "data_store:main-db", "backed_by") in edges


def test_build_graph_ticket_nodes(tmp_path):
    from jig.graph.derive import build_graph
    _write_arch(tmp_path, [_minimal_module("api-client")])
    tickets_path = tmp_path / ".jig" / "store" / "tickets.jsonl"
    tickets_path.parent.mkdir(parents=True, exist_ok=True)
    tickets_path.write_text(
        json.dumps({
            "id": "feat-01",
            "title": "Build API",
            "work_type": "feature",
            "status": "open",
            "created_by": "pm",
            "module_id": "api-client",
            "capability_ids": ["fetch-stories"],
        }) + "\n"
    )
    g = build_graph(tmp_path)
    node_ids = {n.id for n in g.nodes}
    edges = {(e.src, e.dst, e.kind) for e in g.edges}
    assert "ticket:feat-01" in node_ids
    assert ("ticket:feat-01", "module:api-client", "touches") in edges
    assert ("ticket:feat-01", "capability:fetch-stories", "touches") in edges


def test_build_graph_implements_capabilities_edge(tmp_path):
    from jig.graph.derive import build_graph
    _write_arch(tmp_path, [_minimal_module("mod-a", implements_capabilities=["fetch-data"])])
    g = build_graph(tmp_path)
    edges = {(e.src, e.dst, e.kind) for e in g.edges}
    node_ids = {n.id for n in g.nodes}
    assert "capability:fetch-data" in node_ids
    assert ("module:mod-a", "capability:fetch-data", "implements") in edges


# ---- write_graph ------------------------------------------------------------


def test_write_graph_creates_file(tmp_path):
    from jig.graph.derive import write_graph
    _write_arch(tmp_path, [_minimal_module("mod-a")])
    path = write_graph(tmp_path)
    assert path.exists()
    content = yaml.safe_load(path.read_text())
    assert "nodes" in content
    assert "edges" in content
    assert content["spec_version"] == 1


# ---- ticket_impact ----------------------------------------------------------


def _make_graph_for_impact():
    from jig.graph.types import DependencyGraph, Edge, Node
    nodes = [
        Node(id="ticket:t-01", kind="ticket"),
        Node(id="module:api", kind="module"),
        Node(id="module:db", kind="module"),
        Node(id="exposed_api:api:get", kind="exposed_api"),
    ]
    edges = [
        Edge(src="ticket:t-01", dst="module:api", kind="touches"),
        Edge(src="module:api", dst="exposed_api:api:get", kind="exposes"),
        Edge(src="module:db", dst="module:api", kind="consumes"),  # db consumes api
    ]
    return DependencyGraph(generated_at=_ts(), nodes=nodes, edges=edges)


def test_ticket_impact_touched_nodes():
    from jig.graph.derive import ticket_impact
    g = _make_graph_for_impact()
    impact = ticket_impact(g, "t-01")
    touched_ids = {n.id for n in impact.touched}
    assert touched_ids == {"module:api"}


def test_ticket_impact_consumers():
    from jig.graph.derive import ticket_impact
    g = _make_graph_for_impact()
    impact = ticket_impact(g, "t-01")
    # module:db consumes module:api, so module:api has a consumer
    assert "module:api" in impact.consumers
    consumer_ids = {n.id for n in impact.consumers["module:api"]}
    assert "module:db" in consumer_ids


def test_ticket_impact_crossed_boundaries_single_module():
    from jig.graph.derive import ticket_impact
    g = _make_graph_for_impact()
    impact = ticket_impact(g, "t-01", depth=1)
    # At depth=1, we walk from module:api → exposed_api:api:get (not a module)
    # Only 1 module in walked set → crossed_boundaries = 0
    assert impact.crossed_boundaries == 0


def test_ticket_impact_crossed_boundaries_multi_module():
    from jig.graph.types import DependencyGraph, Edge, Node
    from jig.graph.derive import ticket_impact
    nodes = [
        Node(id="ticket:t-02", kind="ticket"),
        Node(id="module:a", kind="module"),
        Node(id="module:b", kind="module"),
    ]
    edges = [
        Edge(src="ticket:t-02", dst="module:a", kind="touches"),
        Edge(src="ticket:t-02", dst="module:b", kind="touches"),
    ]
    g = DependencyGraph(generated_at=_ts(), nodes=nodes, edges=edges)
    impact = ticket_impact(g, "t-02")
    assert impact.crossed_boundaries == 1


def test_ticket_impact_unknown_ticket():
    from jig.graph.derive import ticket_impact
    g = _make_graph_for_impact()
    impact = ticket_impact(g, "no-such-ticket")
    assert impact.touched == []
    assert impact.consumers == {}
    assert impact.crossed_boundaries == 0


def test_ticket_impact_exercised_tracers():
    from jig.graph.types import DependencyGraph, Edge, Node
    from jig.graph.derive import ticket_impact
    nodes = [
        Node(id="ticket:t-03", kind="ticket"),
        Node(id="module:api", kind="module"),
        Node(id="tracer:smoke-01", kind="tracer"),
    ]
    edges = [
        Edge(src="ticket:t-03", dst="module:api", kind="touches"),
        Edge(src="module:api", dst="tracer:smoke-01", kind="exercises"),
    ]
    g = DependencyGraph(generated_at=_ts(), nodes=nodes, edges=edges)
    impact = ticket_impact(g, "t-03", depth=1)
    tracer_ids = {n.id for n in impact.exercised_tracers}
    assert "tracer:smoke-01" in tracer_ids
