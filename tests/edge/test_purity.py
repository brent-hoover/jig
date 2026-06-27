"""EDGE bones — the edge boundary invariant (Epic 9 verify).

"No engine module is imported by ``jig/edge/`` except via the API contract."

Bones holds the strictest honest form of this: ``jig.edge`` imports **only
itself** (the contract is plain DTOs). So the allowlist is just ``jig.edge`` — any
other ``jig.*`` module pulled in or written is a violation. Two complementary
checks:

- ``test_edge_imports_only_itself`` — runtime ``sys.modules`` after importing the
  whole package (module-level + *transitive* imports).
- ``test_edge_has_no_forbidden_import_statements`` — static AST scan of every
  ``jig/edge/*.py``: ``import`` / ``from`` (absolute + relative), lazy
  function-local imports, and constant-string dynamic imports via
  ``importlib.import_module`` / ``__import__`` — including aliased helpers.

MVP expands the allowlist deliberately as it routes CLI/TUI through the daemon
API (see ``architecture/edge-audit.md``). ``_scan_source`` is factored out so the
scanner's coverage of each form is unit-tested directly.
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
    """Resolve a relative import (``level`` leading levels + ``module`` suffix)
    against ``package_parts`` to an absolute dotted module name."""
    kept = package_parts[: len(package_parts) - (level - 1)]
    base = ".".join(kept)
    if module:
        base = f"{base}.{module}" if base else module
    return base


def _const_arg(node: ast.Call, *, kw: str, pos: int, typ: type) -> object | None:
    """A constant arg of type ``typ`` by keyword name or positional index."""
    for keyword in node.keywords:
        if keyword.arg == kw:
            value = keyword.value
            if isinstance(value, ast.Constant) and isinstance(value.value, typ):
                return value.value
            return None
    if len(node.args) > pos:
        value = node.args[pos]
        if isinstance(value, ast.Constant) and isinstance(value.value, typ):
            return value.value
    return None


def _const_int_arg(node: ast.Call, *, kw: str, pos: int) -> int | None:
    value = _const_arg(node, kw=kw, pos=pos, typ=int)
    return value if isinstance(value, int) else None


def _const_str_arg(node: ast.Call, *, kw: str, pos: int) -> str | None:
    value = _const_arg(node, kw=kw, pos=pos, typ=str)
    return value if isinstance(value, str) else None


def _import_fromlist(node: ast.Call) -> list[str]:
    """Constant ``fromlist`` entries of ``__import__`` (list/tuple/set), so
    ``__import__("jig", fromlist=["orchestrator"])`` expands to ``jig.orchestrator``."""
    value: ast.expr | None = None
    for kw in node.keywords:
        if kw.arg == "fromlist":
            value = kw.value
    if value is None and len(node.args) >= 4:
        value = node.args[3]
    if isinstance(value, (ast.List, ast.Tuple, ast.Set)):
        return [
            elt.value
            for elt in value.elts
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
        ]
    return []


def _dynamic_import_package(
    node: ast.Call, package_parts: tuple[str, ...]
) -> tuple[str, ...] | None:
    """The ``package`` for a relative ``import_module`` call (``package=`` keyword
    or 2nd positional; ``__package__`` => this module's). ``None`` if unresolvable."""
    pkg_args: list[ast.expr] = [kw.value for kw in node.keywords if kw.arg == "package"]
    if not pkg_args and len(node.args) >= 2:
        pkg_args = [node.args[1]]
    for value in pkg_args:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return tuple(value.value.split("."))
        if isinstance(value, ast.Name) and value.id == "__package__":
            return package_parts
        return None
    return None


def _dynamic_import_aliases(tree: ast.AST) -> tuple[set[str], set[str]]:
    """Names that refer to ``importlib.import_module`` / ``__import__`` in this
    module — via ``from importlib import import_module as X``, ``from builtins
    import __import__ as Y``, or simple ``f = __import__`` assignments."""
    import_module = {"import_module"}
    dunder = {"__import__"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "importlib":
            for alias in node.names:
                if alias.name == "import_module":
                    import_module.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "builtins":
            for alias in node.names:
                if alias.name == "__import__":
                    dunder.add(alias.asname or alias.name)
        elif isinstance(node, ast.Assign):
            rhs = node.value
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if isinstance(rhs, ast.Name) and rhs.id in import_module:
                import_module.update(targets)
            elif isinstance(rhs, ast.Name) and rhs.id in dunder:
                dunder.update(targets)
            elif isinstance(rhs, ast.Attribute) and rhs.attr == "import_module":
                import_module.update(targets)
    return import_module, dunder


def _scan_source(source: str, package_parts: tuple[str, ...]) -> list[tuple[int, str]]:
    """Return ``(lineno, forbidden-jig-module)`` for every import in ``source``
    that targets a non-allowlisted ``jig.*`` module, across every import form."""
    tree = ast.parse(source)
    import_module_aliases, dunder_aliases = _dynamic_import_aliases(tree)
    offenders: list[tuple[int, str]] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            candidates = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                base = node.module or ""
            else:
                base = _resolve_relative(node.level, node.module or "", package_parts)
            candidates = [base] if base else []
            candidates += [
                f"{base}.{alias.name}" if base else alias.name for alias in node.names
            ]
        elif isinstance(node, ast.Call):
            func = node.func
            is_import_module = (
                isinstance(func, ast.Attribute) and func.attr == "import_module"
            ) or (isinstance(func, ast.Name) and func.id in import_module_aliases)
            is_dunder = isinstance(func, ast.Name) and func.id in dunder_aliases
            # ``name`` may be positional (arg 0) or keyword (import_module(name=...)).
            name = _const_str_arg(node, kw="name", pos=0)
            if not ((is_import_module or is_dunder) and name is not None):
                continue
            if is_dunder:
                level = _const_int_arg(node, kw="level", pos=4) or 0
                base = (
                    _resolve_relative(level, name, package_parts) if level > 0 else name
                )
                candidates = [base] if base else []
                if base:
                    candidates += [f"{base}.{e}" for e in _import_fromlist(node)]
            elif name.startswith("."):
                level = len(name) - len(name.lstrip("."))
                pkg = _dynamic_import_package(node, package_parts)
                if pkg is None:
                    offenders.append(
                        (node.lineno, f"unresolved relative dynamic import {name!r}")
                    )
                    continue
                base = _resolve_relative(level, name[level:], pkg)
                candidates = [base] if base else []
            else:
                candidates = [name]
        else:
            continue

        for module in candidates:
            if module.startswith("jig.") and not _is_allowed(module):
                offenders.append((node.lineno, module))

    return offenders


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
    """Static check over the real package — every import form, including lazy."""
    pkg_dir = pathlib.Path(jig.edge.__file__).parent
    jig_root = pkg_dir.parent
    offenders: list[str] = []

    for py in sorted(pkg_dir.rglob("*.py")):
        module_parts = ("jig", *py.relative_to(jig_root).with_suffix("").parts)
        package_parts = module_parts[:-1]
        for lineno, module in _scan_source(py.read_text(), package_parts):
            offenders.append(f"{py.name}:{lineno} -> {module}")

    assert offenders == [], f"forbidden imports in jig/edge/: {offenders}"


def test_scanner_catches_every_forbidden_import_form() -> None:
    """The scanner detects each evasion form, so the real-package test above can't
    pass merely because a form is unhandled."""
    pkg = ("jig", "edge")
    cases = [
        "import jig.orchestrator",
        "from jig.store import tickets",
        "from jig import orchestrator",  # bare-jig alias expansion
        "from ..orchestrator import Orchestrator",  # relative
        "import importlib\nimportlib.import_module('jig.orchestrator')",  # dynamic
        "import importlib\nimportlib.import_module('..orchestrator', __package__)",  # rel dyn
        "__import__('jig', fromlist=['orchestrator'])",  # fromlist (list)
        "__import__('jig', fromlist={'orchestrator'})",  # fromlist (set)
        "__import__('orchestrator', globals(), locals(), [], 2)",  # __import__ level
        "from importlib import import_module as load\nload('jig.orchestrator')",  # alias
        "im = __import__\nim('jig', fromlist=['store'])",  # assigned alias
        "import importlib\nimportlib.import_module(name='jig.orchestrator')",  # kw name
    ]
    for source in cases:
        found = {module for _, module in _scan_source(source, pkg)}
        assert any(m.startswith(("jig.orchestrator", "jig.store")) for m in found), (
            f"scanner missed a forbidden import in:\n{source}\n-> found {found}"
        )


def test_scanner_allows_edge_internal_imports() -> None:
    pkg = ("jig", "edge")
    for source in (
        "from jig.edge.api import Snapshot",
        "from .api import Snapshot",
        "import importlib\nimportlib.import_module('.api', __package__)",
    ):
        assert _scan_source(source, pkg) == [], source
