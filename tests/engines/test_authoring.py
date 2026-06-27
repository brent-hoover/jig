"""Authoring engines bones — Discovery / Architecture / VD (Epic 6).

Each authoring engine owns exactly one ``project://`` authority (task 2):
Discovery -> ``spec``, Architecture -> ``arch``, Visual Design -> ``design``.
Bones also exposes the existing PO-interview (Discovery) and SA<->operator loop
(Architecture) flows at their new homes (tasks 3-4); the real extraction +
SA-role unification is MVP.
"""

from __future__ import annotations

import pytest

from jig.engines.architecture import ENGINE as ARCH_ENGINE
from jig.engines.authoring import AuthoringEngine
from jig.engines.discovery import ENGINE as DISCOVERY_ENGINE
from jig.engines.visual_design import ENGINE as VD_ENGINE


def test_each_engine_owns_its_authority() -> None:
    assert DISCOVERY_ENGINE.authority == "spec"
    assert ARCH_ENGINE.authority == "arch"
    assert VD_ENGINE.authority == "design"


def test_owns_routes_by_authority() -> None:
    assert DISCOVERY_ENGINE.owns("project://spec/capabilities/login")
    assert not DISCOVERY_ENGINE.owns("project://arch/architecture")
    assert ARCH_ENGINE.owns("project://arch/modules/auth/contracts")
    assert not ARCH_ENGINE.owns("project://spec/name")
    assert VD_ENGINE.owns("project://design/frontend")
    assert not VD_ENGINE.owns("project://spec/name")


def test_authoring_engine_rejects_an_unknown_authority() -> None:
    with pytest.raises(ValueError):
        AuthoringEngine(name="bogus", authority="not-an-authority")


def test_authoring_engine_rejects_non_authoring_authorities() -> None:
    # `plan` and `store` are valid StoreAuthority authorities, but they are not
    # authored by an engine — PM/Build own them. The boundary must reject them.
    for non_authoring in ("plan", "store"):
        with pytest.raises(ValueError):
            AuthoringEngine(name="x", authority=non_authoring)


def test_discovery_exposes_the_po_interview_flow() -> None:
    import jig.init_workflow as canonical
    from jig.engines.discovery import interview

    assert interview.run_po_conversation is canonical.run_po_conversation
    assert interview.next_incomplete_level is canonical.next_incomplete_level


def test_architecture_exposes_the_sa_operator_loop() -> None:
    import jig.init_workflow as canonical
    from jig.engines.architecture import sa_loop

    assert sa_loop.run_sa_conversation is canonical.run_sa_conversation
    assert sa_loop.prompt_sa_confirm is canonical.prompt_sa_confirm
