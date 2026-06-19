"""L1 PO schemas + path helpers + state YAML round-trip (Track B MVP)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from jig.schemas.po import (
    CapabilityRosterEntry,
    DiscoveryDoc,
    DiscoveryPhase,
    DiscoveryState,
    DiscoveryStatus,
    Journey,
    PendingCapability,
    Persona,
)
from jig.spec_loader import (
    discovery_path,
    discovery_playback_path,
    discovery_state_path,
    load_discovery,
    load_discovery_state,
    save_discovery_state,
)


# ---- Persona ---------------------------------------------------------------


def test_persona_round_trip():
    p = Persona(id="merchant", description="The buyer who integrates jig")
    data = p.model_dump(mode="json")
    Persona.model_validate(data)
    assert data["id"] == "merchant"


def test_persona_rejects_non_kebab_id():
    """The brief-format rule: persona ids are kebab-case."""
    with pytest.raises(ValidationError, match="kebab-case"):
        Persona(id="Merchant Account", description="x")


def test_persona_rejects_blank_description():
    with pytest.raises(ValidationError):
        Persona(id="merchant", description="")


# ---- Journey ---------------------------------------------------------------


def test_journey_round_trip():
    j = Journey(
        id="j-merchant-onboarding",
        persona_id="merchant",
        title="Merchant onboarding",
        narrative="Merchant signs up, uploads catalog, installs snippet.",
        capability_ids=["self-serve-signup", "shopify-connect"],
    )
    Journey.model_validate(j.model_dump(mode="json"))


def test_journey_id_must_be_kebab():
    with pytest.raises(ValidationError, match="kebab-case"):
        Journey(
            id="J Merchant Onboarding",
            persona_id="merchant",
            title="x",
            narrative="x",
        )


def test_journey_persona_id_must_be_kebab():
    with pytest.raises(ValidationError, match="kebab-case"):
        Journey(
            id="j-x",
            persona_id="Merchant",
            title="x",
            narrative="x",
        )


def test_journey_capability_ids_must_be_kebab():
    with pytest.raises(ValidationError, match="kebab-case"):
        Journey(
            id="j-x",
            persona_id="merchant",
            title="x",
            narrative="x",
            capability_ids=["Good", "still-bad name"],
        )


# ---- CapabilityRosterEntry -------------------------------------------------


def test_capability_roster_entry_round_trip():
    e = CapabilityRosterEntry(
        id="self-serve-signup",
        description="Self-serve account creation with email",
        journey_ids=["j-merchant-onboarding"],
    )
    CapabilityRosterEntry.model_validate(e.model_dump(mode="json"))


def test_capability_roster_entry_rejects_non_kebab_id():
    with pytest.raises(ValidationError, match="kebab-case"):
        CapabilityRosterEntry(id="SelfServeSignup", description="x")


def test_capability_roster_entry_rejects_non_kebab_journey():
    with pytest.raises(ValidationError, match="kebab-case"):
        CapabilityRosterEntry(
            id="self-serve-signup",
            description="x",
            journey_ids=["bad id"],
        )


# ---- DiscoveryDoc ----------------------------------------------------------


def test_discovery_doc_minimum():
    d = DiscoveryDoc(project_name="jig-search")
    # Default lists are empty — the doc may be partially built mid-walk.
    assert d.personas == []
    assert d.journeys == []
    assert d.capability_roster == []
    DiscoveryDoc.model_validate(d.model_dump(mode="json"))


def test_discovery_doc_full_round_trip():
    d = DiscoveryDoc(
        project_name="jig-search",
        intro="Discovery walk for jig-search.",
        personas=[Persona(id="merchant", description="merchant who integrates jig")],
        journeys=[
            Journey(
                id="j-merchant-onboarding",
                persona_id="merchant",
                title="Onboarding",
                narrative="Signs up, uploads catalog, installs snippet.",
                capability_ids=["self-serve-signup"],
            )
        ],
        capability_roster=[
            CapabilityRosterEntry(
                id="self-serve-signup",
                description="Self-serve account creation",
                journey_ids=["j-merchant-onboarding"],
            )
        ],
    )
    DiscoveryDoc.model_validate(d.model_dump(mode="json"))


# ---- DiscoveryState --------------------------------------------------------


def test_discovery_state_default_in_progress():
    s = DiscoveryState()
    assert s.status == DiscoveryStatus.IN_PROGRESS
    assert s.current is None
    assert s.next_question is None
    assert s.pending_capabilities == []
    DiscoveryState.model_validate(s.model_dump(mode="json"))


def test_discovery_state_rejects_unknown_status():
    with pytest.raises(ValidationError, match="status"):
        DiscoveryState(status="halfway")


def test_discovery_phase_validates_range():
    DiscoveryPhase(persona_id="merchant", journey_id="j-x", phase=5, step=3)
    with pytest.raises(ValidationError):
        DiscoveryPhase(persona_id=None, journey_id=None, phase=0)
    with pytest.raises(ValidationError):
        DiscoveryPhase(persona_id=None, journey_id=None, phase=6)


def test_pending_capability_kebab_validation():
    PendingCapability(id="ok", description="x", journey_id="j-x")
    with pytest.raises(ValidationError):
        PendingCapability(id="Bad Cap", description="x", journey_id="j-x")
    with pytest.raises(ValidationError):
        PendingCapability(id="ok", description="x", journey_id="J X")


# ---- path helpers ----------------------------------------------------------


def test_discovery_path_helper(tmp_path: Path):
    assert discovery_path(tmp_path) == tmp_path / ".jig" / "spec" / "discovery.md"


def test_discovery_state_path_helper(tmp_path: Path):
    assert (
        discovery_state_path(tmp_path)
        == tmp_path / ".jig" / "spec" / "discovery.state.yaml"
    )


def test_discovery_playback_path_helper(tmp_path: Path):
    assert (
        discovery_playback_path(tmp_path, "j-merchant-onboarding")
        == tmp_path
        / ".jig"
        / "spec"
        / "discovery"
        / "playbacks"
        / "j-merchant-onboarding.md"
    )


# ---- save / load round-trip ------------------------------------------------


def test_save_and_load_discovery_state(tmp_path: Path):
    state = DiscoveryState(
        current=DiscoveryPhase(
            persona_id="merchant",
            journey_id="j-merchant-onboarding",
            phase=3,
            step=4,
        ),
        next_question="At the summary screen, what happens if nobody posted yet?",
        phases_completed_this_journey=[1, 2],
        personas_pending=["customer", "maintainer"],
        pending_capabilities=[
            PendingCapability(
                id="scan-overnight-summary",
                description="Scan summary for 'looks off' signals",
                journey_id="j-merchant-onboarding",
            )
        ],
    )
    save_discovery_state(tmp_path, state)
    # File landed at the v2 layout location.
    assert discovery_state_path(tmp_path).is_file()
    loaded = load_discovery_state(tmp_path)
    assert loaded.status == DiscoveryStatus.IN_PROGRESS
    assert loaded.current is not None
    assert loaded.current.persona_id == "merchant"
    assert loaded.current.phase == 3
    assert loaded.next_question is not None
    assert loaded.pending_capabilities[0].id == "scan-overnight-summary"


def test_load_discovery_state_missing_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_discovery_state(tmp_path)


def test_load_discovery_missing_raises(tmp_path: Path):
    """Bones cache: with no projection on disk, loader fails loudly."""
    with pytest.raises(FileNotFoundError):
        load_discovery(tmp_path)
