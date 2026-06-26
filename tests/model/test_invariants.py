"""CORE Model bones — the 5 invariants as pure function signatures (Epic 1, task 4).

Bones phase: the bodies are stubs returning no findings. This test pins the
signatures and the ``Finding`` return type so later MVP work fills in real
graph-query logic without changing the contract.
"""

from __future__ import annotations

import inspect

from jig.model.invariants import (
    Finding,
    conformance,
    containment,
    coverage,
    ownership,
    vocabulary,
)


def test_finding_is_constructible() -> None:
    f = Finding(invariant="coverage", message="orphan capability: foo")
    assert f.invariant == "coverage"
    assert f.message == "orphan capability: foo"
    assert f.subject is None


def test_stub_invariants_return_no_findings() -> None:
    assert coverage(None) == []
    assert conformance(None, None) == []
    assert containment(None, None) == []
    assert vocabulary(None) == []
    assert ownership(None) == []


def test_invariant_signatures_are_pinned() -> None:
    assert list(inspect.signature(coverage).parameters) == ["model"]
    assert list(inspect.signature(conformance).parameters) == ["model", "code"]
    assert list(inspect.signature(containment).parameters) == ["model", "code"]
    assert list(inspect.signature(vocabulary).parameters) == ["model"]
    assert list(inspect.signature(ownership).parameters) == ["model"]
