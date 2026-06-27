"""EDGE bones — the edge boundary invariant (Epic 9 verify).

"No engine module is imported by ``jig/edge/`` except via the API contract."

Bones holds the strictest honest form of this: ``jig.edge`` imports **only
itself** (the contract is plain DTOs). So the allowlist is just ``jig.edge`` — any
other ``jig.*`` module pulled in or written is a violation. Two complementary
checks:

- ``test_edge_imports_only_itself`` — runtime ``sys.modules`` after importing the
  whole package (module-level + *transitive* imports).
- ``test_edge_has_no_forbidden_import_statements`` — static AST scan of every
  ``jig/edge/*.py`` import (absolute, ``from jig import X``, relative, lazy).

MVP expands the allowlist deliberately, one reviewed edge-appropriate dependency
at a time, as it routes CLI/TUI through the daemon API (see
``architecture/edge-audit.md`` for the surface it must retire). Keeping the
allowlist minimal now means the test can never bless a front-door module that is
itself still impure.
"""

from __future__ import annotations

import ast
import pathlib
import subprocess
import sys

import jig.edge

# The only jig package the edge may touch today: itself. (MVP grows this.)
_ALLOWED_PREFIXES: tuple[str, ...] = ("jig.edge",)


def _is_allowed(module: str) -> bool:
    return any(module == a or module.startswith(a + ".") for a in _ALLOWED_PREFIXES)


def _resolve_relative(level: int, module: str, package_parts: tuple[str, ...]) -> str:
    """Resolve a relative import (``level`` leading dots + ``module`` suffix)
    against ``package_parts`` to an absolute dotted module name."""
    kept = package_parts[: len(package_parts) - (level - 1)]
    base = ".".join(kept)
    if module:
        base = f"{base}.{module}" if base else module
    return base


def _dynamic_import_package(
    node: ast.Call, package_parts: tuple[str, ...]
) -> tuple[str, ...] | None:
    """The ``package`` for a relative ``import_module`` call: the ``package=``
    keyword or 2nd positional arg, resolving ``__package__`` to this module's
    package. Returns ``None`` when it can't be resolved statically."""
    candidates_for_pkg: list[ast.expr] = [
        kw.value for kw in node.keywords if kw.arg == "package"
    ]
    if not candidates_for_pkg and len(node.args) >= 2:
        candidates_for_pkg = [node.args[1]]
    for value in candidates_for_pkg:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return tuple(value.value.split("."))
        if isinstance(value, ast.Name) and value.id == "__package__":
            return package_parts
        return None
    return None


def _import_fromlist(node: ast.Call) -> list[str]:
    """Constant ``fromlist`` entries of ``__import__(name, ..., fromlist, ...)``
    — the ``fromlist`` keyword or the 4th positional arg. Lets
    ``__import__("jig", fromlist=["orchestrator"])`` expand to ``jig.orchestrator``."""
    value: ast.expr | None = None
    for kw in node.keywords:
        if kw.arg == "fromlist":
            value = kw.value
    if value is None and len(node.args) >= 4:
        value = node.args[3]
    if isinstance(value, (ast.List, ast.Tuple)):
        return [
            elt.value
            for elt in value.elts
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
        ]
    return []


def test_edge_imports_only_itself() -> None:
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
        f"jig.edge imported non-edge modules: {out.stdout}"
    )


def test_edge_has_no_forbidden_import_statements() -> None:
    """Static check — every import statement: absolute, ``from jig import X``,
    relative (``from ..orchestrator import X``), and lazy/function-local."""
    pkg_dir = pathlib.Path(jig.edge.__file__).parent
    jig_root = pkg_dir.parent  # the jig/ directory
    offenders: list[str] = []

    for py in sorted(pkg_dir.rglob("*.py")):
        # The package containing this module (drop the module name / __init__).
        module_parts = ("jig", *py.relative_to(jig_root).with_suffix("").parts)
        package_parts = module_parts[:-1]
        tree = ast.parse(py.read_text(), filename=str(py))

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                candidates = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0:
                    base = node.module or ""
                else:
                    # Resolve relative imports against this module's package.
                    base = _resolve_relative(
                        node.level, node.module or "", package_parts
                    )
                # Expand `from base import a, b` -> base.a, base.b so that
                # `from jig import orchestrator` is caught, not just `base`.
                candidates = [base] if base else []
                candidates += [
                    f"{base}.{alias.name}" if base else alias.name
                    for alias in node.names
                ]
            elif isinstance(node, ast.Call):
                # Constant-string dynamic imports: importlib.import_module("jig.x"),
                # __import__("jig.x"), or relative import_module("..x", __package__).
                # (Non-constant module names aren't statically resolvable; the
                # runtime check is the backstop for those.)
                func = node.func
                is_dynamic_import = (
                    isinstance(func, ast.Name)
                    and func.id in ("__import__", "import_module")
                ) or (isinstance(func, ast.Attribute) and func.attr == "import_module")
                if not (
                    is_dynamic_import
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                ):
                    continue
                name = node.args[0].value
                if not name.startswith("."):
                    candidates = [name]
                    # __import__("jig", fromlist=["orchestrator"]) -> jig.orchestrator
                    if isinstance(func, ast.Name) and func.id == "__import__":
                        candidates += [f"{name}.{e}" for e in _import_fromlist(node)]
                else:
                    # Relative dynamic import — resolve against the `package` arg
                    # (2nd positional or keyword; `__package__` => this module's).
                    level = len(name) - len(name.lstrip("."))
                    pkg = _dynamic_import_package(node, package_parts)
                    if pkg is None:
                        offenders.append(
                            f"{py.name}:{node.lineno} -> unresolved relative "
                            f"dynamic import {name!r}"
                        )
                        continue
                    resolved = _resolve_relative(level, name[level:], pkg)
                    candidates = [resolved] if resolved else []
            else:
                continue

            for module in candidates:
                if module.startswith("jig.") and not _is_allowed(module):
                    offenders.append(f"{py.name}:{node.lineno} -> {module}")

    assert offenders == [], f"forbidden imports in jig/edge/: {offenders}"
