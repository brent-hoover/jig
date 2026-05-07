"""Render a ``DataContract`` as a self-contained Pydantic class source string.

Per ``docs/v2.0/agent-leverage/problem.md`` §6 (Track I MVP): the first
renderer in the translation-between-formalisms commitment. One source
(``DataContract``), one derived view (Pydantic class). The next
renderers (OpenAPI, SQL DDL, GraphQL SDL, sequence diagrams) ship
opportunistically — see the v2-plan sequencing table.

Design choices:

- **Self-contained output.** The rendered string includes the
  ``from pydantic import ...`` line so the operator can drop the
  source straight into a fresh module without remembering the
  imports.
- **Source URI in a header comment.** The contract's ``schema_ref``
  (or a placeholder when absent) lands as a comment at the top of
  the rendered file. Per docs §6's mitigation: every rendered
  artifact carries the source URI so the operator can compare and
  catch hallucinations.
- **No type validation on the field-type strings.** MVP accepts any
  string and renders it verbatim as the type annotation. Bad
  expressions ("not a real type") only surface at import time —
  that's the same error the operator would get from a hand-written
  Pydantic class. Future MVP lift could parse the type strings
  with ``ast.parse`` to catch obviously-bad ones earlier.
- **Strict by default** (``model_config = ConfigDict(extra="forbid")``).
  The whole point of generating a contract from a contract is to
  catch drift; allowing extras would silently accept fields the
  contract doesn't authorize.
- **Field-name validation.** Field identifiers are validated
  conservatively (Python identifier shape, no dunders). Generated
  source that won't import is the worst possible failure mode —
  the operator sees an opaque traceback instead of a clear
  renderer error.
"""
from __future__ import annotations

import keyword
import re

from jig.schemas.arch import DataContract


# Python identifier per the language reference. ``\w`` permits unicode
# identifiers in 3.x; we constrain to ASCII for predictability — the
# generated class lives in the operator's source tree and ASCII names
# read uniformly.
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validate_field_name(name: str) -> None:
    """Reject field names that would produce broken Pydantic source.

    Raises ``ValueError`` with a category-tagged message so the CLI
    layer can surface the cause cleanly. Categories:

    - "invalid identifier" — doesn't match Python identifier shape
    - "dunder" — has leading-and-trailing underscores (collides
      with Pydantic's internals; reserved by language convention)
    - "reserved" — Python keyword (would syntax-error on import)
    """
    if not _IDENT_RE.match(name):
        raise ValueError(
            f"DataContract field {name!r} is not a valid Python "
            "identifier; the renderer can't produce importable source "
            "from it. Pick a name matching ``[A-Za-z_][A-Za-z0-9_]*``."
        )
    if name.startswith("__") and name.endswith("__"):
        raise ValueError(
            f"DataContract field {name!r} is a dunder name; these "
            "collide with Pydantic internals. Rename the field."
        )
    if keyword.iskeyword(name):
        raise ValueError(
            f"DataContract field {name!r} is a Python keyword; "
            "the rendered class wouldn't parse. Rename the field."
        )


def _pascal_case(s: str) -> str:
    """Convert kebab-case / snake-case / mixed identifiers to PascalCase.

    The class name is the contract id PascalCased so the rendered
    source matches the convention an operator hand-writes (e.g.
    ``product-row`` → ``ProductRow``). Splits on ``-`` and ``_``; we
    don't try to split on case boundaries because that would mangle
    ids the operator already PascalCased.
    """
    parts = re.split(r"[-_]+", s)
    return "".join(p[:1].upper() + p[1:] for p in parts if p)


def render_pydantic_from_data_contract(contract: DataContract) -> str:
    """Return Pydantic class source rendering ``contract.fields``.

    Raises ``ValueError`` when the contract has no inline ``fields``
    payload to render (the MVP renderer requires it; URI-based
    resolution lands later) or when any field name fails the
    identifier checks above.
    """
    if not contract.fields:
        raise ValueError(
            f"DataContract {contract.id!r} has no inline ``fields`` "
            "payload — the Track-I-MVP renderer needs the field map "
            "to render a Pydantic class. Add a ``fields`` dict to "
            "the contract or wait for URI-based schema resolution."
        )

    for name in contract.fields:
        _validate_field_name(name)

    class_name = _pascal_case(contract.id)
    source_uri = contract.schema_ref or "(no schema_ref recorded)"

    body_lines: list[str] = []
    if contract.description:
        # Single-line docstring — Pydantic field declarations follow.
        body_lines.append(f'    """{contract.description}"""')
        body_lines.append("")
    body_lines.append('    model_config = ConfigDict(extra="forbid")')
    body_lines.append("")
    for field_name, type_str in contract.fields.items():
        body_lines.append(f"    {field_name}: {type_str}")

    rendered = (
        f"# Generated by jig render pydantic — DO NOT EDIT by hand.\n"
        f"# Source contract: {contract.id}\n"
        f"# Source URI:      {source_uri}\n"
        f"#\n"
        f"# Per docs/v2.0/agent-leverage/problem.md §6, every rendered\n"
        f"# artifact carries the source URI so divergence between\n"
        f"# the generated view and the contract stays detectable.\n"
        f"#\n"
        f"# Regenerate by re-running ``module_set_data_contract`` on\n"
        f"# the source contract, or by invoking the\n"
        f"# ``arch_regenerate_pydantic_models`` MCP tool / the\n"
        f"# ``jig render pydantic`` CLI.\n"
        f"\n"
        f"from pydantic import BaseModel, ConfigDict\n"
        f"\n"
        f"\n"
        f"class {class_name}(BaseModel):\n"
        + "\n".join(body_lines)
        + "\n"
    )
    return rendered


__all__ = ["render_pydantic_from_data_contract"]
