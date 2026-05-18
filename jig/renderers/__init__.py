"""Translation renderers — Track I MVP §6.

Per ``docs/v2.0/agent-leverage/problem.md`` §6: given any contract, render
it as Pydantic / SQL / OpenAPI / sequence diagram / etc. — all from
one source of truth. MVP ships **one** renderer
(Pydantic-from-data-contract); the others land opportunistically when
the synchronization cost of hand-maintaining each format exceeds the
build cost of the renderer.

Public surface:

- ``render_pydantic_from_data_contract`` — render a ``DataContract``
  with an inline ``fields`` payload as a self-contained Pydantic
  class source string. The output carries the contract's ``schema_ref``
  as a header comment so the operator can trace the generated class
  back to its source of truth.
"""

from __future__ import annotations

from jig.renderers.pydantic_from_data_contract import (
    render_pydantic_from_data_contract,
)

__all__ = ["render_pydantic_from_data_contract"]
