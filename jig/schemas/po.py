"""PO output schemas — L0 Project + L1 Discovery + L2 SuitesIndex + L3 SuiteBriefStructured.

L3 reuses ``jig.spec_schema.StructuredSpec`` shape — the structured
projection of a single suite brief is the same shape as the v1 monolithic
spec, just scoped to one suite. We re-export it here for discoverability;
suite-scope and federation-level differences live in the file layout, not
the schema.

``SuitesIndex`` is the L2 artifact that the L3 PO READS to know its
suite's capability allowlist. The L2 PO authoring side is out of bones
scope (synthetic operator hand-writes ``suites.yaml``); the schema lives
here so L3 can validate against it.

L1 schemas (Persona / Journey / CapabilityRosterEntry / DiscoveryDoc /
DiscoveryState) back the L1 PO discovery conversation — see
``docs/v2.0/multi-level-spec/design.md`` §"L1 — Discovery". The L1 PO writes
``discovery.md`` (rendered from ``DiscoveryDoc``) and tracks in-flight
state in ``discovery.state.yaml`` (``DiscoveryState``); per-journey
playbacks land under ``.jig/spec/discovery/playbacks/``.
"""
from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator

from jig.schemas._validators import (
    validate_kebab_id,
    validate_kebab_id_list,
    validate_tz_aware,
)
from jig.spec_schema import StructuredSpec

__all__ = [
    "Project",
    "ProductNonGoal",
    "Suite",
    "SuitesIndex",
    "StructuredSpec",
    "Persona",
    "Journey",
    "CapabilityRosterEntry",
    "DiscoveryDoc",
    "DiscoveryPhase",
    "DiscoveryStatus",
    "PendingCapability",
    "CapabilityCandidate",
    "DiscoveryState",
    "StateDivergence",
    "StateDivergenceKind",
    "OntologyTerm",
    "Ontology",
    "PendingOntologyTerm",
]


# Local alias — keeps existing call-sites in this module compact and stable
# while delegating to the shared validator package.
def _validate_kebab(value: str, field: str) -> str:
    return validate_kebab_id(value, field)


class ProductNonGoal(BaseModel):
    """A project-level product non-goal captured in the L0 pitch.

    Re-used at L2 for ``crosscutting_non_goals`` — same shape, different
    scope (the suites-spanning non-goals an operator wants tracked
    alongside the suite list).
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1, description="kebab-case stable id")
    text: str = Field(..., min_length=1)
    rationale: str | None = None

    @field_validator("id")
    @classmethod
    def _kebab_id(cls, v: str) -> str:
        return _validate_kebab(v, "ProductNonGoal.id")


class Project(BaseModel):
    """L0 — the project pitch.

    Written by the L0 PO in 3-5 turns. Captures pitch + problem + audience +
    product-level non-goals. Does not mention features, suites, or
    capabilities — those land at L1/L2/L3.
    """

    model_config = ConfigDict(extra="forbid")

    spec_version: int = 2
    name: str = Field(..., min_length=1)
    pitch: str = Field(..., min_length=1, description="One-sentence pitch.")
    problem: str = Field(
        ...,
        min_length=1,
        description=(
            "One paragraph: what's broken in the world this fixes, and why "
            "it matters to the audience."
        ),
    )
    audience: str = Field(
        ...,
        min_length=1,
        description="One paragraph: who uses this and what they're trying to do.",
    )
    non_goals: list[ProductNonGoal] = Field(default_factory=list)
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @field_validator("generated_at")
    @classmethod
    def _tz_generated_at(cls, v: datetime) -> datetime:
        return validate_tz_aware(v, "Project.generated_at")


class Suite(BaseModel):
    """One suite entry in ``.jig/spec/suites.yaml``.

    The L2 PO authors this; the L3 PO reads it to scope a single suite's
    brief. ``capabilities`` is the allowlist — L3 may only elaborate
    capabilities whose ids appear here. Adding a capability not on this
    list is a gap that must go back to L1; for bones the L3 PO simply
    rejects it.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1, description="kebab-case suite id")
    title: str = Field(..., min_length=1)
    summary: str = Field(..., min_length=1)
    capabilities: list[str] = Field(
        default_factory=list,
        description="L1 capability ids assigned to this suite.",
    )

    @field_validator("id")
    @classmethod
    def _kebab_id(cls, v: str) -> str:
        return _validate_kebab(v, "Suite.id")

    @field_validator("capabilities")
    @classmethod
    def _kebab_capabilities(cls, v: list[str]) -> list[str]:
        return validate_kebab_id_list(v, "Suite.capabilities")


class SuitesIndex(BaseModel):
    """L2 — the suite organization for a project.

    Authoring is out of bones scope. The synthetic operator hand-writes
    a minimal ``suites.yaml`` and the L3 PO reads it via
    ``jig.spec_loader.load_suites_index`` to look up its suite entry +
    capability allowlist.
    """

    model_config = ConfigDict(extra="forbid")

    spec_version: int = 2
    suites: list[Suite] = Field(default_factory=list)
    crosscutting_non_goals: list[ProductNonGoal] = Field(default_factory=list)

    def suite_by_id(self, suite_id: str) -> Suite | None:
        """Return the suite with this id, or ``None``."""
        for s in self.suites:
            if s.id == suite_id:
                return s
        return None


# ---- L1 Discovery ---------------------------------------------------------


class Persona(BaseModel):
    """One persona row in the L1 discovery doc.

    Per design.md §"L1 — Discovery": a one-line description plus a
    kebab-case id used as the anchor (``{#merchant}``) the journeys
    reference. The L1 PO captures personas during Phase 1 and 2 of
    the journey-walk.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., description="kebab-case persona id")
    description: str = Field(
        ...,
        min_length=1,
        description="One-line persona description in the operator's vocabulary.",
    )

    @field_validator("id")
    @classmethod
    def _kebab_id(cls, v: str) -> str:
        return _validate_kebab(v, "Persona.id")


class Journey(BaseModel):
    """One journey block in the L1 discovery doc.

    Per design.md §"L1 — Discovery": a journey is a narrative tied to
    one persona, plus the list of capability ids it implies. The L1 PO
    commits one journey per Phase-5 playback. Journey ids start with
    ``j-`` and contain a persona keyword (e.g. ``j-merchant-onboarding``);
    the kebab-case rule covers everything after the prefix.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., description="kebab-case journey id; convention starts with 'j-'")
    persona_id: str = Field(..., description="The Persona.id this journey belongs to.")
    title: str = Field(
        ...,
        min_length=1,
        description="Short title in the operator's words (heading text).",
    )
    narrative: str = Field(
        ...,
        min_length=1,
        description=(
            "Free-prose paragraph(s) describing the journey step by step in "
            "the operator's vocabulary."
        ),
    )
    capability_ids: list[str] = Field(
        default_factory=list,
        description=(
            "Kebab-case capability ids implied by this journey. Every entry "
            "should also appear in the doc's ``capability_roster`` with this "
            "journey id in its ``journeys`` list — the renderer + finalize "
            "validator enforce that round-trip."
        ),
    )

    @field_validator("id")
    @classmethod
    def _kebab_id(cls, v: str) -> str:
        return _validate_kebab(v, "Journey.id")

    @field_validator("persona_id")
    @classmethod
    def _kebab_persona(cls, v: str) -> str:
        return _validate_kebab(v, "Journey.persona_id")

    @field_validator("capability_ids")
    @classmethod
    def _kebab_capability_ids(cls, v: list[str]) -> list[str]:
        for c in v:
            _validate_kebab(c, "Journey.capability_ids[]")
        return v


class CapabilityRosterEntry(BaseModel):
    """One capability row in the L1 discovery roster.

    Per design.md §"Capability roster": a deduplicated, sorted-by-first-
    mention list at the bottom of ``discovery.md``. Each entry tracks
    which journey ids surfaced it so L2 can group capabilities by shared
    journey context.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., description="kebab-case capability id")
    description: str = Field(
        ...,
        min_length=1,
        description="One-line capability description in the operator's verbs.",
    )
    journey_ids: list[str] = Field(
        default_factory=list,
        description="Journey ids that surfaced this capability.",
    )

    @field_validator("id")
    @classmethod
    def _kebab_id(cls, v: str) -> str:
        return _validate_kebab(v, "CapabilityRosterEntry.id")

    @field_validator("journey_ids")
    @classmethod
    def _kebab_journey_ids(cls, v: list[str]) -> list[str]:
        for j in v:
            _validate_kebab(j, "CapabilityRosterEntry.journey_ids[]")
        return v


class DiscoveryDoc(BaseModel):
    """L1 — the synthesized discovery document.

    Rendered to ``.jig/spec/discovery.md`` (committed history) at
    ``discovery_finalize`` time. The on-disk markdown also contains an
    intro paragraph above ``## Personas`` (captured separately via
    ``discovery_set_intro``); the schema models everything below it.
    """

    model_config = ConfigDict(extra="forbid")

    spec_version: int = 1
    project_name: str = Field(..., min_length=1)
    intro: str = Field(
        default="",
        description=(
            "Optional preface paragraph rendered above '## Personas'. "
            "Empty string skips the preface — the headings still render."
        ),
    )
    personas: list[Persona] = Field(default_factory=list)
    journeys: list[Journey] = Field(default_factory=list)
    capability_roster: list[CapabilityRosterEntry] = Field(default_factory=list)
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @field_validator("generated_at")
    @classmethod
    def _tz_generated_at(cls, v: datetime) -> datetime:
        return validate_tz_aware(v, "DiscoveryDoc.generated_at")


# ---- L1 in-flight conversation state --------------------------------------


class DiscoveryPhase(BaseModel):
    """Pointer to the current spot in the 5-phase walk.

    Phases 1-5 mirror design.md §"L1 PO behavior". ``step`` is the
    sub-step index within Phase 3 (Walk); other phases set it to 0.
    persona_id / journey_id are nullable for Phase 1 (frame) which runs
    before the first persona is locked in.
    """

    model_config = ConfigDict(extra="forbid")

    persona_id: str | None = None
    journey_id: str | None = None
    phase: int = Field(..., ge=1, le=5)
    step: int = Field(default=0, ge=0)


class PendingCapability(BaseModel):
    """A capability the L1 PO extracted mid-walk but hasn't committed yet.

    Stashed during Phase 3/4 so an interrupted session doesn't lose the
    operator-confirmed extraction. ``discovery_add_capability`` (Phase 5)
    promotes these to roster entries and clears the stash.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., description="kebab-case capability id")
    description: str = Field(..., min_length=1)
    journey_id: str = Field(..., description="The journey this surfaced from.")

    @field_validator("id")
    @classmethod
    def _kebab_id(cls, v: str) -> str:
        return _validate_kebab(v, "PendingCapability.id")

    @field_validator("journey_id")
    @classmethod
    def _kebab_journey(cls, v: str) -> str:
        return _validate_kebab(v, "PendingCapability.journey_id")


class DiscoveryStatus:
    """Allowed values for ``DiscoveryState.status``.

    A class rather than ``Enum`` so the YAML round-trip stays plain
    strings (the rest of the v2 PO schemas follow the same pattern —
    string literals on disk, validation via ``Literal``).
    """

    IN_PROGRESS = "in_progress"
    FINALIZED = "finalized"


class CapabilityCandidate(BaseModel):
    """An in-flight capability extraction during a Phase-3 walk.

    Track B Final addition. Where ``PendingCapability`` records a
    confirmed extraction (Phase-3/4 → Phase-5 promote), a
    ``CapabilityCandidate`` records the L1 PO's *current attempt* at
    capturing a capability mid-walk, including whether the operator has
    confirmed it yet. Crash-recovery uses this to skip already-confirmed
    candidates on resume — re-asking "did you mean X?" after the operator
    just said yes is jarring.

    The ``confirmed`` flag is what makes resume non-destructive: the L1
    PO that comes back up reads ``partial_walk`` and only re-prompts on
    the unconfirmed entries.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., description="kebab-case capability id")
    description: str = Field(..., min_length=1)
    journey_id: str = Field(
        ..., description="The journey currently being walked."
    )
    confirmed: bool = Field(
        default=False,
        description=(
            "True once the operator has confirmed the candidate; resume "
            "skips re-asking confirmed entries."
        ),
    )

    @field_validator("id")
    @classmethod
    def _kebab_id(cls, v: str) -> str:
        return _validate_kebab(v, "CapabilityCandidate.id")

    @field_validator("journey_id")
    @classmethod
    def _kebab_journey(cls, v: str) -> str:
        return _validate_kebab(v, "CapabilityCandidate.journey_id")


class DiscoveryState(BaseModel):
    """L1 in-flight conversation state — ``.jig/spec/discovery.state.yaml``.

    Per design.md §"L1 conversation state and resume": rewritten after
    every meaningful operator turn so the L1 PO can resume mid-walk after
    a daemon restart or operator pause. The in-flight state is distinct
    from the committed ``discovery.md`` — the latter only gains entries
    at Phase-5 commit time.
    """

    model_config = ConfigDict(extra="forbid")

    spec_version: int = 1
    status: str = Field(
        default=DiscoveryStatus.IN_PROGRESS,
        description="One of 'in_progress' | 'finalized'.",
    )
    current: DiscoveryPhase | None = Field(
        default=None,
        description="Current position in the 5-phase walk; None pre-Phase-1.",
    )
    next_question: str | None = Field(
        default=None,
        description=(
            "The exact question the L1 PO is about to ask, captured before "
            "the ``ask_question`` call so resume can replay it without "
            "re-deriving (and possibly drifting)."
        ),
    )
    phases_completed_this_journey: list[int] = Field(default_factory=list)
    journeys_completed_this_persona: list[str] = Field(default_factory=list)
    personas_completed: list[str] = Field(default_factory=list)
    personas_pending: list[str] = Field(default_factory=list)
    pending_capabilities: list[PendingCapability] = Field(default_factory=list)
    # Track B Final — in-flight Phase-3 walk candidates. Lets resume
    # continue from the same capability without re-confirming entries the
    # operator already said yes to.
    partial_walk: list[CapabilityCandidate] = Field(default_factory=list)
    # Track B Final — sha256 of ``discovery.md`` at last save. Resume
    # compares against the current file digest to detect operator
    # hand-edits between sessions (concurrent-edit reconciliation). Empty
    # when no discovery.md existed at save time.
    discovery_doc_digest: str = Field(default="")
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @field_validator("updated_at")
    @classmethod
    def _tz_updated_at(cls, v: datetime) -> datetime:
        return validate_tz_aware(v, "DiscoveryState.updated_at")

    @field_validator("status")
    @classmethod
    def _known_status(cls, v: str) -> str:
        allowed = {DiscoveryStatus.IN_PROGRESS, DiscoveryStatus.FINALIZED}
        if v not in allowed:
            raise ValueError(
                f"DiscoveryState.status must be one of {sorted(allowed)!r}, "
                f"got {v!r}"
            )
        return v


# ---- L1 resume-from-state divergence detection ----------------------------


class StateDivergenceKind:
    """Allowed values for ``StateDivergence.kind`` (Track B Final).

    Plain strings (matches DiscoveryStatus pattern) so the YAML
    round-trip stays human-readable.
    """

    STALE_JOURNEY = "stale-journey"
    CONCURRENT_EDIT = "concurrent-edit"
    PARTIAL_WALK_ORPHAN = "partial-walk-orphan"


_DIVERGENCE_VALUES = frozenset({
    StateDivergenceKind.STALE_JOURNEY,
    StateDivergenceKind.CONCURRENT_EDIT,
    StateDivergenceKind.PARTIAL_WALK_ORPHAN,
})


class StateDivergence(BaseModel):
    """One divergence between L1 in-flight state and ``discovery.md``.

    Track B Final — discovered by ``validate_state_consistency``. Each
    divergence carries a kind tag, a human-readable detail string, and a
    suggested resolution mode the operator can pick from when running
    ``discovery_resume``. Operator-driven for Final scope; LLM-driven
    reconciliation is v2.x.
    """

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(
        ...,
        description=(
            "One of 'stale-journey' | 'concurrent-edit' | "
            "'partial-walk-orphan'."
        ),
    )
    detail: str = Field(
        ...,
        min_length=1,
        description="One-line explanation of what diverged.",
    )
    suggested_resolution: str = Field(
        ...,
        min_length=1,
        description=(
            "One of 'prefer-state' | 'prefer-doc' | 'abandon-state' | "
            "'reanchor'."
        ),
    )

    @field_validator("kind")
    @classmethod
    def _known_kind(cls, v: str) -> str:
        if v not in _DIVERGENCE_VALUES:
            raise ValueError(
                f"StateDivergence.kind must be one of "
                f"{sorted(_DIVERGENCE_VALUES)!r}, got {v!r}"
            )
        return v


# ---- Project ontology -----------------------------------------------------


class OntologyTerm(BaseModel):
    """One entry in the project ontology — operator's domain vocabulary.

    Per design.md §"Project ontology — capturing the operator's domain
    vocabulary": ubiquitous-language entries surfaced during PO
    discovery so every downstream agent (SA, VD, PM, dev, reviewer)
    uses consistent terminology. Stored on disk as markdown sections
    keyed by ``term`` heading; ``examples`` render as a bullet list
    under ``**Examples:**`` when present.

    ``term`` is preserved as the operator wrote it (lowercased for
    lookup but the original casing surfaces in the rendered heading).
    No kebab-case rule — domain words may include spaces and quotes
    ("looks off" signals).
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


class PendingOntologyTerm(BaseModel):
    """A term the L1 PO surfaced mid-walk, waiting for Phase-5 confirmation.

    Per design.md: PO doesn't break the journey-walk flow to confirm
    every term. Tentative entries land here via ``ontology_stash_term``;
    the Phase-5 playback surfaces them for operator review and the
    confirmed ones get promoted to the markdown ontology via
    ``ontology_add_term``.

    ``context`` carries the surrounding-conversation snippet (or a
    one-line cue) so the L1 PO can re-anchor the operator at playback
    time without re-deriving the prompt from scratch.
    """

    model_config = ConfigDict(extra="forbid")

    term: str = Field(..., min_length=1)
    context: str = Field(
        ...,
        min_length=1,
        description=(
            "Where the term came up — a journey id, the prior operator "
            "answer, or any cue that lets the PO re-anchor at Phase 5."
        ),
    )


class Ontology(BaseModel):
    """The project's domain vocabulary as captured at finalize time.

    Lives at ``.jig/spec/ontology.md`` (markdown) but the structured
    form is convenient for downstream resolution (``ontology_lookup``)
    and for round-trip testing of the markdown renderer.
    """

    model_config = ConfigDict(extra="forbid")

    spec_version: int = 1
    terms: list[OntologyTerm] = Field(default_factory=list)

    def by_term(self, term: str) -> OntologyTerm | None:
        """Case-insensitive lookup by ``term`` heading."""
        needle = term.strip().lower()
        for t in self.terms:
            if t.term.lower() == needle:
                return t
        return None
