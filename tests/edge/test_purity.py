"""EDGE bones — the edge boundary invariant (Epic 9 verify).

"No engine module is imported by ``jig/edge/`` except via the API contract."

Enforced as **default-deny** two ways that complement each other:

- ``test_edge_imports_only_edge_appropriate_modules`` — a runtime ``sys.modules``
  check after importing the whole package, catching module-level + *transitive*
  imports.
- ``test_edge_has_no_forbidden_import_statements`` — a static AST scan of every
  ``jig/edge/*.py`` import statement, catching *lazy* (function-local) imports a
  runtime check would miss.

Only edge-appropriate ``jig`` modules are allowed; everything else (every engine,
store, MCP handler, workflow, agent, domain type) is a violation by default, so
neither check false-negatives as the repo grows. See ``architecture/edge-audit.md``.
"""

from __future__ import annotations

import ast
import pathlib
import subprocess
import sys

import jig.edge

# The ONLY jig modules the edge may import (architecture/edge-audit.md "keep"
# list). MVP expands this deliberately as it routes CLI/TUI through the daemon
# API. Everything else under jig.* is an engine internal.
_ALLOWED_PREFIXES = (
    "jig.edge",
    "jig.daemon",
    "jig.ws_server",
    "jig.config",
    "jig.events",
    "jig.logging_setup",
    "jig.safe_path",
    "jig.container",
    "jig.dev_env",
    "jig.story",
    "jig.tui",
    "jig.issues.cli",
    "jig.sim.cli",
)


def _is_allowed(module: str) -> bool:
    return any(module == a or module.startswith(a + ".") for a in _ALLOWED_PREFIXES)


def test_edge_imports_only_edge_appropriate_modules() -> None:
    """Runtime check — module-level + transitive imports."""
    code = (
        "import importlib, pkgutil, sys, jig.edge\n"
        "for m in pkgutil.walk_packages(jig.edge.__path__, prefix='jig.edge.'):\n"
        "    importlib.import_module(m.name)\n"
        f"allowed = {_ALLOWED_PREFIXES!r}\n"
        "def ok(m):\n"
        "    return any(m == a or m.startswith(a + '.') for a in allowed)\n"
        "leaked = sorted(m for m in sys.modules if m.startswith('jig.') and not ok(m))\n"
        "print(leaked)\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "[]", (
        f"jig.edge imported non-edge-appropriate modules: {out.stdout}"
    )


def test_edge_has_no_forbidden_import_statements() -> None:
    """Static check — every import statement, including lazy/function-local."""
    pkg_dir = pathlib.Path(jig.edge.__file__).parent
    offenders: list[str] = []

    for py in sorted(pkg_dir.rglob("*.py")):
        tree = ast.parse(py.read_text(), filename=str(py))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            # level == 0 -> absolute import; relative imports stay inside jig.edge.
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules = [node.module]
            else:
                continue

            for module in modules:
                if module.startswith("jig.") and not _is_allowed(module):
                    offenders.append(f"{py.name}:{node.lineno} -> {module}")

    assert offenders == [], f"forbidden imports in jig/edge/: {offenders}"
