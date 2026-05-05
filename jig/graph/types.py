"""Graph types for the jig dependency graph (Phase 3.7).

Node ids use the ``<kind>:<scope>`` convention:
  ``module:api-client``, ``exposed_api:api-client:get_story``,
  ``emitted_event:story-store:story.created``, ``ticket:feat-01``.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Node(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1)
    kind: Literal[
        "module", "capability", "epic", "ticket", "tracer",
        "exposed_api", "emitted_event", "data_store",
        "behavioral_contract", "data_contract",
        "owned_collection", "external_dependency",
        "risk", "journey",
    ]
    title: str | None = None
    uri: str | None = None


class Edge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    src: str = Field(..., min_length=1)
    dst: str = Field(..., min_length=1)
    kind: Literal[
        "owns", "exposes", "emits", "consumes",
        "calls", "depends_on", "backed_by",
        "implements", "covers", "touches",
        "exercises", "validates", "blocks", "uses",
    ]


class DependencyGraph(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spec_version: int = 1
    generated_at: datetime
    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)

    def neighbors(
        self,
        node_id: str,
        depth: int = 1,
        kind: str | None = None,
    ) -> set[str]:
        """Return outgoing-reachable node ids within ``depth`` hops.

        ``kind`` filters the result to nodes of that kind. The origin
        node is never included in the returned set.
        """
        if depth <= 0:
            return set()
        result: set[str] = set()
        frontier: set[str] = {node_id}
        for _ in range(depth):
            next_frontier: set[str] = set()
            for src in frontier:
                for edge in self.edges:
                    if edge.src == src and edge.dst not in result and edge.dst != node_id:
                        result.add(edge.dst)
                        next_frontier.add(edge.dst)
            frontier = next_frontier
            if not frontier:
                break
        if kind is not None:
            kind_map = {n.id: n.kind for n in self.nodes}
            result = {nid for nid in result if kind_map.get(nid) == kind}
        return result

    def consumers_of(self, node_id: str) -> set[str]:
        """Return all node ids with an outgoing edge pointing TO ``node_id``."""
        return {edge.src for edge in self.edges if edge.dst == node_id}

    def reachable_from(self, node_id: str) -> set[str]:
        """Full transitive closure of outgoing-reachable nodes from ``node_id``.

        The origin node is never included in the returned set.
        """
        visited: set[str] = {node_id}  # pre-visit origin to block cycle-back
        result: set[str] = set()
        queue: deque[str] = deque([node_id])
        while queue:
            current = queue.popleft()
            for edge in self.edges:
                if edge.src == current and edge.dst not in visited:
                    visited.add(edge.dst)
                    result.add(edge.dst)
                    queue.append(edge.dst)
        return result


class TicketImpact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticket_id: str
    generated_at: datetime
    touched: list[Node] = Field(default_factory=list)
    consumers: dict[str, list[Node]] = Field(default_factory=dict)
    exercised_tracers: list[Node] = Field(default_factory=list)
    crossed_boundaries: int = Field(default=0, ge=0)


__all__ = ["DependencyGraph", "Edge", "Node", "TicketImpact"]
