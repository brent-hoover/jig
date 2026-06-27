"""CORE Model MVP (#215) — the deterministic invariants as pure graph queries.

``coverage`` / ``containment`` / ``vocabulary`` range over a pure ``Model``
aggregate of model.md entities + first-class trace edges. Tested with synthetic
models (no I/O) — wiring real artifacts into a ``Model`` is downstream
(Enforcement / Reconciliation MVP). ``conformance`` / ``ownership`` stay stubs
(Final).
"""

from __future__ import annotations

from jig.model import (
    Boundary,
    Dependency,
    Model,
    OntologyHome,
    Trace,
    TraceKind,
    containment,
    coverage,
    vocabulary,
)


# --- coverage ----------------------------------------------------------------


def test_coverage_clean_model_has_no_findings() -> None:
    model = Model(
        capabilities=("cap-login",),
        contracts=("api-auth",),
        journeys=("j-signup",),
        suites=("s-onboarding",),
        traces=(
            Trace(src="cap-login", dst="api-auth", kind=TraceKind.REALIZED_BY),
            Trace(src="j-signup", dst="s-onboarding", kind=TraceKind.COVERED_BY),
        ),
    )
    assert coverage(model) == []


def test_coverage_flags_orphan_capability() -> None:
    model = Model(capabilities=("cap-login",))
    findings = coverage(model)
    assert [f.subject for f in findings] == ["cap-login"]
    assert findings[0].invariant == "coverage"


def test_coverage_flags_uncovered_journey() -> None:
    model = Model(journeys=("j-signup",))
    assert [f.subject for f in coverage(model)] == ["j-signup"]


def test_coverage_flags_orphans_in_both_directions() -> None:
    # A contract realizing no capability and a suite covering no journey are
    # orphans too ("no orphans either direction").
    model = Model(contracts=("api-unused",), suites=("s-unused",))
    subjects = {f.subject for f in coverage(model)}
    assert subjects == {"api-unused", "s-unused"}


def test_coverage_ignores_traces_to_undeclared_endpoints() -> None:
    # A trace pointing at a contract that doesn't exist must NOT satisfy the
    # capability's coverage (a dangling edge isn't realization).
    model = Model(
        capabilities=("cap-login",),
        traces=(
            Trace(src="cap-login", dst="ghost-contract", kind=TraceKind.REALIZED_BY),
        ),
    )
    assert [f.subject for f in coverage(model)] == ["cap-login"]


def test_coverage_ignores_traces_from_undeclared_endpoints() -> None:
    # A trace from a non-declared capability must NOT rescue a contract from
    # being an orphan.
    model = Model(
        contracts=("api-auth",),
        traces=(Trace(src="ghost-cap", dst="api-auth", kind=TraceKind.REALIZED_BY),),
    )
    assert [f.subject for f in coverage(model)] == ["api-auth"]


# --- containment -------------------------------------------------------------


def test_containment_clean_model_has_no_findings() -> None:
    model = Model(
        boundaries=(
            Boundary(id="auth", owner="po", contracts=("api-auth",)),
            Boundary(id="web", owner="po"),
        ),
        dependencies=(
            Dependency(consumer="web", provider="auth", contract="api-auth"),
        ),
    )
    assert containment(model) == []


def test_containment_flags_dependency_on_undeclared_contract() -> None:
    model = Model(
        boundaries=(
            Boundary(id="auth", owner="po", contracts=("api-auth",)),
            Boundary(id="web", owner="po"),
        ),
        # web depends on a contract auth does not expose.
        dependencies=(
            Dependency(consumer="web", provider="auth", contract="api-secret"),
        ),
    )
    findings = containment(model)
    assert len(findings) == 1
    assert findings[0].invariant == "containment"
    assert findings[0].subject == "web"


def test_containment_flags_dependency_on_unknown_boundary() -> None:
    model = Model(
        boundaries=(Boundary(id="web", owner="po"),),
        dependencies=(Dependency(consumer="web", provider="ghost", contract="api-x"),),
    )
    assert len(containment(model)) == 1


def test_containment_flags_dependency_from_unknown_consumer() -> None:
    # A dependency whose consumer boundary isn't declared is malformed too.
    model = Model(
        boundaries=(Boundary(id="auth", owner="po", contracts=("api-auth",)),),
        dependencies=(
            Dependency(consumer="ghost", provider="auth", contract="api-auth"),
        ),
    )
    findings = containment(model)
    assert len(findings) == 1
    assert findings[0].subject == "ghost"


# --- vocabulary --------------------------------------------------------------


def test_vocabulary_clean_ontology_has_no_findings() -> None:
    model = Model(
        ontology=(
            OntologyHome(term="Candidate", home="spec/ontology.md"),
            OntologyHome(term="Offer", home="spec/ontology.md"),
        )
    )
    assert vocabulary(model) == []


def test_vocabulary_flags_a_concept_with_more_than_one_home() -> None:
    model = Model(
        ontology=(
            OntologyHome(term="Candidate", home="schemas/arch.py"),
            OntologyHome(term="Candidate", home="schemas/po.py"),
        )
    )
    findings = vocabulary(model)
    assert len(findings) == 1
    assert findings[0].invariant == "vocabulary"
    assert findings[0].subject == "candidate"  # normalized


def test_vocabulary_is_case_insensitive_on_term() -> None:
    model = Model(
        ontology=(
            OntologyHome(term="candidate", home="a"),
            OntologyHome(term="Candidate", home="b"),
        )
    )
    assert len(vocabulary(model)) == 1


def test_vocabulary_same_term_same_home_is_not_a_violation() -> None:
    # Listed twice but one canonical home — fine.
    model = Model(
        ontology=(
            OntologyHome(term="Candidate", home="spec/ontology.md"),
            OntologyHome(term="Candidate", home="spec/ontology.md"),
        )
    )
    assert vocabulary(model) == []
