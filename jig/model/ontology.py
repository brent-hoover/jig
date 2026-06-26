"""The per-project ``Ontology`` — the shared domain vocabulary.

Canonical home for ``OntologyTerm``. This concept was previously forked between
``jig/schemas/arch.py`` (module glossary entry) and ``jig/schemas/po.py``
(operator's domain vocabulary). They are the same concept at different layers;
per the vocabulary invariant ("one concept, one home") it now has a single
definition here, and both schema modules re-export it.

Pure: no I/O imports.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class OntologyTerm(BaseModel):
    """One entry in the project ontology — a domain term in the operator's
    vocabulary, referenced by every downstream artifact (SA, VD, PM, dev,
    reviewer) and by per-module glossaries so terminology stays consistent.

    ``term`` is preserved as written (no kebab-case rule — domain words may
    include spaces and quotes). ``examples`` are optional usage illustrations.
    """

    model_config = ConfigDict(extra="forbid")

    term: str = Field(..., min_length=1, description="The operator's word.")
    definition: str = Field(
        ...,
        min_length=1,
        description="One paragraph definition in the operator's vocabulary.",
    )
    examples: list[str] = Field(
        default_factory=list,
        description="Optional usage examples, one per bullet.",
    )
