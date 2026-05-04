"""Load structured spec artifacts from disk.

Top-level (v1 monolithic):
- ``.jig/spec/project.structured.yaml`` — ``StructuredSpec``

L2 / L3 (v2 multi-level):
- ``.jig/spec/suites.yaml`` — ``SuitesIndex`` (L2 PO authors; L3 PO
  reads to scope its brief)
- ``.jig/spec/suites/<id>/brief.md`` — L3 markdown brief
- ``.jig/spec/suites/<id>/spec.structured.yaml`` — L3 structured spec
  (same shape as v1 ``StructuredSpec``, scoped to one suite)

SA (v2):
- ``.jig/spec/architecture.yaml`` — project-level ``Architecture``
- ``.jig/spec/modules/<m>/contracts.yaml`` — per-module ``ContractsFile``

PM (v2):
- ``.jig/plan/build-plan.yaml`` — project-level ``BuildPlan`` (Track F1)

Thin helpers — no I/O beyond read+parse. Writers live with their authoring
modules (``po_l0_mcp.py``, ``po_l3_mcp.py``, ``sa_mcp.py``); the build-plan
writer is co-located here because Track F bones has no PM agent yet — the
synthetic operator calls ``write_build_plan`` directly.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import yaml

from jig.atomic import atomic_write_text
from jig.schemas.arch import Architecture, ContractsFile
from jig.schemas.plan import BuildPlan
from jig.schemas.po import DiscoveryDoc, DiscoveryState, SuitesIndex
from jig.spec_schema import StructuredSpec

_SPEC_RELATIVE = Path(".jig") / "spec" / "project.structured.yaml"
_SUITES_INDEX_RELATIVE = Path(".jig") / "spec" / "suites.yaml"
_ARCHITECTURE_RELATIVE = Path(".jig") / "spec" / "architecture.yaml"
_BUILD_PLAN_RELATIVE = Path(".jig") / "plan" / "build-plan.yaml"
_DISCOVERY_MD_RELATIVE = Path(".jig") / "spec" / "discovery.md"
_DISCOVERY_STATE_RELATIVE = Path(".jig") / "spec" / "discovery.state.yaml"


def spec_path(project_root: Path) -> Path:
    """Return the on-disk path to the structured spec for a project root."""
    return project_root / _SPEC_RELATIVE


def load_structured_spec(project_root: Path) -> tuple[StructuredSpec, Path]:
    """Load and validate the structured spec.

    Returns ``(spec, source_path)``. Raises ``FileNotFoundError`` if the
    spec file is missing.
    """
    src = spec_path(project_root)
    if not src.is_file():
        raise FileNotFoundError(f"structured spec not found at {src}")
    data = yaml.safe_load(src.read_text()) or {}
    return StructuredSpec.model_validate(data), src


# ---- v2 multi-level paths -------------------------------------------------


def suites_index_path(project_root: Path) -> Path:
    """``.jig/spec/suites.yaml`` — the L2 suite index."""
    return project_root / _SUITES_INDEX_RELATIVE


def load_suites_index(project_root: Path) -> SuitesIndex:
    """Load and validate ``suites.yaml``.

    Raises ``FileNotFoundError`` if absent — the L3 PO is dependent on
    L2 having written it first, so absence is an error rather than a
    silent empty default.
    """
    src = suites_index_path(project_root)
    if not src.is_file():
        raise FileNotFoundError(f"suites index not found at {src}")
    data = yaml.safe_load(src.read_text()) or {}
    return SuitesIndex.model_validate(data)


def suite_dir(project_root: Path, suite_id: str) -> Path:
    """``.jig/spec/suites/<suite_id>/`` — the per-suite artifact dir."""
    return project_root / ".jig" / "spec" / "suites" / suite_id


def suite_brief_path(project_root: Path, suite_id: str) -> Path:
    """``.jig/spec/suites/<suite_id>/brief.md``."""
    return suite_dir(project_root, suite_id) / "brief.md"


def suite_structured_path(project_root: Path, suite_id: str) -> Path:
    """``.jig/spec/suites/<suite_id>/spec.structured.yaml``."""
    return suite_dir(project_root, suite_id) / "spec.structured.yaml"


# ---- v2 SA paths ----------------------------------------------------------


def architecture_path(project_root: Path) -> Path:
    """``.jig/spec/architecture.yaml`` — the project-level SA artifact.

    v1 ``init_mcp.handle_arch_set_field`` writes a free-form dict to the
    same path; v2 ``Architecture`` is a strict Pydantic shape. The v2
    plan is a clean break (no migration) so the two artifacts don't
    coexist within a single project — only within the codebase.
    """
    return project_root / _ARCHITECTURE_RELATIVE


def load_architecture(project_root: Path) -> Architecture:
    """Load and validate ``architecture.yaml``.

    Raises ``FileNotFoundError`` if absent — the v2 SA writes it from
    scratch on first spawn, so callers that need to read it (reviewers,
    PM planner, etc.) treat absence as "SA hasn't run yet" rather than
    silently defaulting to an empty arch.
    """
    src = architecture_path(project_root)
    if not src.is_file():
        raise FileNotFoundError(f"architecture.yaml not found at {src}")
    data = yaml.safe_load(src.read_text()) or {}
    return Architecture.model_validate(data)


def module_dir(project_root: Path, module_id: str) -> Path:
    """``.jig/spec/modules/<module_id>/`` — the per-module artifact dir."""
    return project_root / ".jig" / "spec" / "modules" / module_id


def module_contracts_path(project_root: Path, module_id: str) -> Path:
    """``.jig/spec/modules/<module_id>/contracts.yaml``."""
    return module_dir(project_root, module_id) / "contracts.yaml"


def load_module_contracts(project_root: Path, module_id: str) -> ContractsFile:
    """Load and validate one module's ``contracts.yaml``.

    Raises ``FileNotFoundError`` if absent. Same rationale as
    ``load_architecture``: callers that need it treat absence as
    "module not authored yet" rather than silent default.
    """
    src = module_contracts_path(project_root, module_id)
    if not src.is_file():
        raise FileNotFoundError(
            f"contracts.yaml for module {module_id!r} not found at {src}"
        )
    data = yaml.safe_load(src.read_text()) or {}
    return ContractsFile.model_validate(data)


# ---- v2 PM paths ----------------------------------------------------------


def build_plan_path(project_root: Path) -> Path:
    """``.jig/plan/build-plan.yaml`` — the PM's living build plan.

    Track F bones: the synthetic operator hand-writes this file via
    ``write_build_plan`` since the Planner agent (F2) hasn't landed yet.
    The Coordinator (F4) reads it to materialize tickets into the store.
    """
    return project_root / _BUILD_PLAN_RELATIVE


def load_build_plan(project_root: Path) -> BuildPlan:
    """Load and validate ``build-plan.yaml``.

    Raises ``FileNotFoundError`` if absent — the Coordinator treats
    absence as "no plan yet" via try/except rather than silently
    defaulting to an empty plan, mirroring ``load_architecture``.
    """
    src = build_plan_path(project_root)
    if not src.is_file():
        raise FileNotFoundError(f"build-plan.yaml not found at {src}")
    data = yaml.safe_load(src.read_text()) or {}
    return BuildPlan.model_validate(data)


def write_build_plan(project_root: Path, plan: BuildPlan) -> None:
    """Atomically write ``plan`` to ``.jig/plan/build-plan.yaml``.

    Track F1 bones: deterministic key order via ``sort_keys=False`` so
    diffs across writes stay readable for the synthetic operator
    iterating on a scenario. Bones is a one-shot writer with no
    merge/amend logic — the Planner agent (F2) gets that in MVP.
    """
    payload = yaml.safe_dump(plan.model_dump(mode="json"), sort_keys=False)
    atomic_write_text(build_plan_path(project_root), payload)


# ---- v2 L1 PO paths -------------------------------------------------------


def discovery_path(project_root: Path) -> Path:
    """``.jig/spec/discovery.md`` — committed L1 personas / journeys / roster.

    Per design.md §"L1 — Discovery". The L1 PO writes this only at
    Phase-5 commit time (and at ``discovery_finalize``); in-flight
    state lives in ``discovery.state.yaml``.
    """
    return project_root / _DISCOVERY_MD_RELATIVE


def discovery_state_path(project_root: Path) -> Path:
    """``.jig/spec/discovery.state.yaml`` — L1 in-flight conversation state.

    Rewritten after every meaningful operator turn so the L1 PO can
    resume mid-walk after a daemon restart or operator pause.
    """
    return project_root / _DISCOVERY_STATE_RELATIVE


def discovery_playback_path(project_root: Path, journey_id: str) -> Path:
    """``.jig/spec/discovery/playbacks/<journey_id>.md``.

    The audit trail of the Phase-5 playback the L1 PO read back to the
    operator before committing the journey to ``discovery.md``.
    """
    return (
        project_root
        / ".jig" / "spec" / "discovery" / "playbacks" / f"{journey_id}.md"
    )


def load_discovery(project_root: Path) -> DiscoveryDoc:
    """Load and validate ``discovery.md``'s structured projection.

    ``discovery.md`` is markdown; bones doesn't ship a parser yet (the
    full L1 brief parser is a later track). For now this is a thin
    helper that rebuilds a ``DiscoveryDoc`` from a sibling YAML cache
    when present. Raises ``FileNotFoundError`` if neither is on disk.

    Out-of-scope-for-bones: parsing the markdown back into a doc. The
    L1 PO writes the doc once per finalize and downstream readers go
    through the markdown directly (or wait for the parser).
    """
    # Bones-scope: there's no markdown-back-to-doc parser yet. Reading
    # the cached YAML projection is what the synthetic operator and
    # downstream tests need; markdown reads stay raw-text.
    cache = project_root / ".jig" / "spec" / "discovery.structured.yaml"
    if not cache.is_file():
        raise FileNotFoundError(
            f"discovery cache not found at {cache} "
            "(write_discovery_doc has not been called yet)"
        )
    data = yaml.safe_load(cache.read_text()) or {}
    return DiscoveryDoc.model_validate(data)


def load_discovery_state(project_root: Path) -> DiscoveryState:
    """Load and validate ``discovery.state.yaml``.

    Raises ``FileNotFoundError`` if absent — the L1 PO calls this on
    resume; absence means "no prior session" rather than an empty default
    so callers can branch cleanly. (The state file is created lazily on
    the first ``save_discovery_state`` call, not at session start.)
    """
    src = discovery_state_path(project_root)
    if not src.is_file():
        raise FileNotFoundError(f"discovery state not found at {src}")
    data = yaml.safe_load(src.read_text()) or {}
    return DiscoveryState.model_validate(data)


def save_discovery_state(project_root: Path, state: DiscoveryState) -> None:
    """Atomically write ``state`` to ``.jig/spec/discovery.state.yaml``.

    Refreshes ``updated_at`` on every save so a stale state file is
    visible at a glance. ``sort_keys=False`` keeps the YAML readable
    for the operator who may inspect it between sessions.
    """
    state.updated_at = datetime.now(timezone.utc)
    payload = yaml.safe_dump(state.model_dump(mode="json"), sort_keys=False)
    atomic_write_text(discovery_state_path(project_root), payload)
