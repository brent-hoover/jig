"""Constructors for ``project://spec/...`` URIs (PO output)."""

from __future__ import annotations

from jig.uri.constructors._segments import seg


def spec_project_uri() -> str:
    """``project://spec/project`` — the L0 project pitch."""
    return "project://spec/project"


def spec_discovery_uri() -> str:
    """``project://spec/discovery`` — the L1 discovery doc."""
    return "project://spec/discovery"


def spec_persona_uri(persona_id: str) -> str:
    """``project://spec/discovery/personas/<id>`` — one persona block."""
    return f"project://spec/discovery/personas/{seg(persona_id, 'persona_id')}"


def spec_journey_uri(journey_id: str) -> str:
    """``project://spec/discovery/journeys/<id>`` — one journey block."""
    return f"project://spec/discovery/journeys/{seg(journey_id, 'journey_id')}"


def spec_playback_uri(journey_id: str) -> str:
    """``project://spec/discovery/playbacks/<journey-id>`` — Phase-5 playback."""
    return f"project://spec/discovery/playbacks/{seg(journey_id, 'journey_id')}"


def spec_ontology_uri() -> str:
    """``project://spec/ontology`` — the project domain vocabulary."""
    return "project://spec/ontology"


def spec_ontology_term_uri(term: str) -> str:
    """``project://spec/ontology/terms/<term>`` — a single term entry."""
    return f"project://spec/ontology/terms/{seg(term, 'term')}"


def spec_suites_uri() -> str:
    """``project://spec/suites`` — the L2 suites index."""
    return "project://spec/suites"


def spec_suite_uri(suite_id: str) -> str:
    """``project://spec/suites/<id>`` — one suite within the index."""
    return f"project://spec/suites/{seg(suite_id, 'suite_id')}"


def spec_suite_brief_uri(suite_id: str) -> str:
    """``project://spec/suites/<id>/brief`` — the L3 suite brief markdown."""
    return f"project://spec/suites/{seg(suite_id, 'suite_id')}/brief"


def spec_suite_structured_uri(suite_id: str) -> str:
    """``project://spec/suites/<id>/spec`` — the L3 structured projection."""
    return f"project://spec/suites/{seg(suite_id, 'suite_id')}/spec"


def spec_capability_uri(suite_id: str, cap_id: str) -> str:
    """``project://spec/suites/<id>/capabilities/<id>`` — one capability."""
    return (
        f"project://spec/suites/{seg(suite_id, 'suite_id')}"
        f"/capabilities/{seg(cap_id, 'cap_id')}"
    )


def spec_behavior_uri(suite_id: str, cap_id: str, behavior_id: str) -> str:
    """``project://spec/suites/<id>/capabilities/<id>/behaviors/<id>``."""
    return (
        f"project://spec/suites/{seg(suite_id, 'suite_id')}"
        f"/capabilities/{seg(cap_id, 'cap_id')}"
        f"/behaviors/{seg(behavior_id, 'behavior_id')}"
    )


def spec_ac_uri(suite_id: str, cap_id: str, behavior_id: str, ac_id: str) -> str:
    """``project://spec/suites/<s>/capabilities/<c>/behaviors/<b>/ac/<id>``."""
    return (
        f"project://spec/suites/{seg(suite_id, 'suite_id')}"
        f"/capabilities/{seg(cap_id, 'cap_id')}"
        f"/behaviors/{seg(behavior_id, 'behavior_id')}"
        f"/ac/{seg(ac_id, 'ac_id')}"
    )


def spec_project_structured_uri() -> str:
    """``project://spec/project_structured`` — federated top-level."""
    return "project://spec/project_structured"
