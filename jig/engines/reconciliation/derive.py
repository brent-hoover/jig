"""Derive the actual dependency graph from code.

Spike 2 (#202) resolved: **use grimp.** grimp is the import-graph engine behind
import-linter — purpose-built for "actual structure from code", fast (a few
hundred modules in ~0.03s), and it resolves the ``from pkg import module``
module-vs-name ambiguity correctly (a naive ``ast`` walk can't without
reimplementing module resolution). Its only requirement, ``typing-extensions``,
is already a Jig dependency.

``derive_actual_graph(code_path)`` builds the grimp graph of the package rooted
at ``code_path`` and projects it onto the reconciliation ``DependencyGraph``
(module-name nodes + intra-package import edges; external/stdlib imports are
excluded). The declared-vs-actual ``diff`` (Epic 7) then surfaces drift.
"""

from __future__ import annotations

import sys
from pathlib import Path

import grimp

from jig.engines.reconciliation.graph import DependencyGraph


def derive_actual_graph(code_path: Path) -> DependencyGraph:
    """Build the actual module dependency graph for the package at ``code_path``.

    ``code_path`` is the package directory (e.g. ``<repo>/jig``); its name is the
    importable package and its parent must be on ``sys.path`` for grimp to locate
    the modules. The parent is added transiently and removed afterward.
    """
    code_path = code_path.resolve()
    package = code_path.name
    parent = str(code_path.parent)

    # Make the given path's parent the FIRST finder entry so the package at
    # ``code_path`` wins over any same-named package already on sys.path, then
    # restore sys.path exactly.
    original = list(sys.path)
    sys.path.insert(0, parent)
    try:
        graph = grimp.build_graph(package)
    finally:
        sys.path[:] = original

    internal = set(graph.modules)
    edges = frozenset(
        (module, imported)
        for module in internal
        for imported in graph.find_modules_directly_imported_by(module)
        if imported in internal  # intra-package only; exclude stdlib/external
    )
    return DependencyGraph.of(nodes=internal, edges=edges)
