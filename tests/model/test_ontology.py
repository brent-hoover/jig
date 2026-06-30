"""CORE Model bones — the deduplicated ``OntologyTerm`` (Epic 1, task 2-3).

``OntologyTerm`` was forked between ``schemas/arch.py`` and ``schemas/po.py``.
The canonical home is now ``jig/model/ontology.py``; both schema modules
re-export it so the concept has exactly one definition (vocabulary invariant).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from jig.model.ontology import OntologyTerm
from jig.schemas import arch as arch_schemas
from jig.schemas import po as po_schemas


def test_canonical_term_carries_examples() -> None:
    bare = OntologyTerm(term="Candidate", definition="An applicant.")
    assert bare.examples == []

    rich = OntologyTerm(
        term="Candidate", definition="An applicant.", examples=["a", "b"]
    )
    assert rich.examples == ["a", "b"]


def test_arch_and_po_reexport_the_same_class() -> None:
    assert arch_schemas.OntologyTerm is OntologyTerm
    assert po_schemas.OntologyTerm is OntologyTerm


def test_term_and_definition_must_be_non_empty() -> None:
    with pytest.raises(ValidationError):
        OntologyTerm(term="", definition="d")
    with pytest.raises(ValidationError):
        OntologyTerm(term="t", definition="")


def test_extra_keys_are_forbidden() -> None:
    with pytest.raises(ValidationError, match="extra"):
        OntologyTerm(term="t", definition="d", bogus="x")
