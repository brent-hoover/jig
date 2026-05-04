"""Programmatic URI constructors per artifact kind (Track A Final).

One constructor per artifact kind so agents and code never string-template
``project://...`` URIs by hand.  Helpers are organized by authority — see
``jig/uri/README.md`` for the grouping convention and naming rules.

Each constructor:

* validates path-segment inputs against the kebab-case grammar
* returns a string (callers wanting a parsed form do
  ``parse_project_uri(s)`` themselves)
* has a one-line docstring
* has a paired test in ``tests/test_uri_constructors.py``

Names follow ``<authority>_<artifact>_uri`` so the surface is flat and
predictable: ``arch_module_uri``, ``store_ticket_uri``, etc.
"""
from __future__ import annotations

from jig.uri.constructors.arch import (
    arch_architecture_uri,
    arch_behavioral_contract_uri,
    arch_contracts_uri,
    arch_data_contract_uri,
    arch_integration_ac_uri,
    arch_module_uri,
    arch_risk_uri,
    arch_shared_contract_uri,
)
from jig.uri.constructors.design import (
    design_frontend_uri,
    design_system_brand_uri,
    design_system_components_uri,
    design_system_tokens_uri,
    design_wireframe_uri,
)
from jig.uri.constructors.plan import (
    plan_build_uri,
    plan_deferred_uri,
    plan_epic_uri,
    plan_layer_uri,
    plan_ticket_uri,
)
from jig.uri.constructors.spec import (
    spec_ac_uri,
    spec_behavior_uri,
    spec_capability_uri,
    spec_discovery_uri,
    spec_journey_uri,
    spec_ontology_term_uri,
    spec_ontology_uri,
    spec_persona_uri,
    spec_playback_uri,
    spec_project_structured_uri,
    spec_project_uri,
    spec_suite_brief_uri,
    spec_suite_structured_uri,
    spec_suite_uri,
    spec_suites_uri,
)
from jig.uri.constructors.store import (
    store_event_uri,
    store_thread_entry_uri,
    store_thread_uri,
    store_ticket_uri,
)

__all__ = [
    # spec
    "spec_project_uri",
    "spec_discovery_uri",
    "spec_persona_uri",
    "spec_journey_uri",
    "spec_playback_uri",
    "spec_ontology_uri",
    "spec_ontology_term_uri",
    "spec_suites_uri",
    "spec_suite_uri",
    "spec_suite_brief_uri",
    "spec_suite_structured_uri",
    "spec_capability_uri",
    "spec_behavior_uri",
    "spec_ac_uri",
    "spec_project_structured_uri",
    # arch
    "arch_architecture_uri",
    "arch_module_uri",
    "arch_contracts_uri",
    "arch_shared_contract_uri",
    "arch_integration_ac_uri",
    "arch_behavioral_contract_uri",
    "arch_data_contract_uri",
    "arch_risk_uri",
    # design
    "design_wireframe_uri",
    "design_system_tokens_uri",
    "design_system_components_uri",
    "design_system_brand_uri",
    "design_frontend_uri",
    # plan
    "plan_build_uri",
    "plan_epic_uri",
    "plan_layer_uri",
    "plan_ticket_uri",
    "plan_deferred_uri",
    # store
    "store_ticket_uri",
    "store_thread_uri",
    "store_thread_entry_uri",
    "store_event_uri",
]
