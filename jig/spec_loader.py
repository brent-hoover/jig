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
from jig.schemas.dev_env import DevManifest
from jig.schemas.design_system import (
    DEFAULT_BRAND,
    DEFAULT_COMPONENTS,
    DEFAULT_DESIGN_SYSTEM,
    DEFAULT_TOKENS,
    Brand,
    ComponentLibrary,
    DesignSystem,
    Tokens,
)
from jig.schemas.frontend import FrontendSpec
from jig.schemas.plan import BuildPlan
from jig.schemas.po import (
    DiscoveryDoc,
    DiscoveryState,
    Ontology,
    SuitesIndex,
)
from jig.spec_schema import StructuredSpec

_SPEC_RELATIVE = Path(".jig") / "spec" / "project.structured.yaml"
_SUITES_INDEX_RELATIVE = Path(".jig") / "spec" / "suites.yaml"
_ARCHITECTURE_RELATIVE = Path(".jig") / "spec" / "architecture.yaml"
_BUILD_PLAN_RELATIVE = Path(".jig") / "plan" / "build-plan.yaml"
_DISCOVERY_MD_RELATIVE = Path(".jig") / "spec" / "discovery.md"
_DISCOVERY_STATE_RELATIVE = Path(".jig") / "spec" / "discovery.state.yaml"
_ONTOLOGY_RELATIVE = Path(".jig") / "spec" / "ontology.md"
_DEV_MANIFEST_RELATIVE = Path(".jig") / "dev" / "manifest.yaml"
_FRONTEND_SPEC_RELATIVE = Path(".jig") / "spec" / "frontend.yaml"


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


def save_architecture(project_root: Path, arch: Architecture) -> None:
    """Atomically write ``arch`` to ``.jig/spec/architecture.yaml``.

    Used by the SA MVP incremental authoring path — each upsert call
    loads, mutates, then writes. ``sort_keys=False`` mirrors the bones
    one-shot writer's choice so diffs across writes stay readable for
    the operator inspecting the file mid-discovery.
    """
    payload = yaml.safe_dump(arch.model_dump(mode="json"), sort_keys=False)
    atomic_write_text(architecture_path(project_root), payload)


def save_module_contracts(
    project_root: Path, module_id: str, contracts: ContractsFile
) -> None:
    """Atomically write ``contracts`` to ``modules/<module_id>/contracts.yaml``.

    Companion to ``save_architecture`` for the SA MVP incremental path.
    The module dir is created on demand so the first upsert against a
    new module doesn't fail on a missing parent.
    """
    payload = yaml.safe_dump(contracts.model_dump(mode="json"), sort_keys=False)
    atomic_write_text(module_contracts_path(project_root, module_id), payload)


# ---- v2 cascade-after-impossible-spike paths -----------------------------


def cascades_dir(project_root: Path) -> Path:
    """``.jig/arch/cascades/`` — per ``docs/sa-architecture/design.md``.

    Each ``confirmed_impossible`` spike emits one cascade-proposal YAML
    here named ``<risk-id>-<timestamp>.yaml``. Operator hand-edits the
    file (MVP scope) to set real dispositions, then re-runs SA. The
    timestamp suffix preserves the audit trail across multiple cascade
    rounds for the same risk (re-spike → confirm impossible again).
    """
    return project_root / ".jig" / "arch" / "cascades"


def cascade_proposal_path(
    project_root: Path, risk_id: str, ts: str
) -> Path:
    """Deterministic path for one cascade proposal.

    ``ts`` is the writer-generated timestamp (an ISO-like string with
    filesystem-safe characters; the writer formats as ``YYYYMMDDTHHMMSS``).
    Round-trippable from the on-disk filename so loaders can rebuild
    the path without globbing.
    """
    return cascades_dir(project_root) / f"{risk_id}-{ts}.yaml"


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


# ---- v2 dev-environment paths (Track E MVP) ------------------------------


def dev_manifest_path(project_root: Path) -> Path:
    """``.jig/dev/manifest.yaml`` — derived dev-environment manifest.

    Generated artifact (not hand-edited). Re-projected from
    ``architecture.yaml`` whenever the architecture changes; the
    ``dev_derive_manifest`` MCP tool / ``jig dev manifest`` CLI
    re-runs the derivation.
    """
    return project_root / _DEV_MANIFEST_RELATIVE


def load_dev_manifest(project_root: Path) -> DevManifest:
    """Load and validate ``manifest.yaml``.

    Raises ``FileNotFoundError`` if absent — callers (orchestrator's
    per-agent provisioning hook, the orphan tracker) treat absence as
    "no dev provisioning declared yet" rather than silently defaulting
    to an empty manifest. Same shape contract as ``load_architecture``.
    """
    src = dev_manifest_path(project_root)
    if not src.is_file():
        raise FileNotFoundError(f"dev manifest not found at {src}")
    data = yaml.safe_load(src.read_text()) or {}
    return DevManifest.model_validate(data)


def save_dev_manifest(project_root: Path, manifest: DevManifest) -> None:
    """Atomically write ``manifest`` to ``.jig/dev/manifest.yaml``.

    The parent dir is created on demand so the first save against a
    fresh project doesn't fail on a missing ``.jig/dev/``.
    """
    payload = yaml.safe_dump(manifest.model_dump(mode="json"), sort_keys=False)
    atomic_write_text(dev_manifest_path(project_root), payload)


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


# ---- v2 project ontology paths -------------------------------------------


def ontology_path(project_root: Path) -> Path:
    """``.jig/spec/ontology.md`` — operator-edit-friendly domain vocabulary.

    Per design.md §"Project ontology". The L1 PO is the primary author
    (terms get captured during journey walks); SA / VD / PM / dev /
    reviewer agents read it for terminology consistency.
    """
    return project_root / _ONTOLOGY_RELATIVE


def load_ontology(project_root: Path) -> Ontology:
    """Parse ``ontology.md`` into an ``Ontology``.

    Raises ``FileNotFoundError`` if the file is absent — callers
    branch on absence (e.g., the L1 PO writes a fresh one when no
    prior session exists) rather than silently defaulting to empty.

    Markdown shape (per design.md):

        # <project> — Domain Vocabulary

        <optional preface paragraphs>

        ## Terms

        ### <term>
        <definition paragraph>

        **Examples:**
        - example one
        - example two

        ### <next term>
        ...
    """
    src = ontology_path(project_root)
    if not src.is_file():
        raise FileNotFoundError(f"ontology not found at {src}")
    # Local import to avoid circular: po_ontology_mcp depends on this
    # module (path helpers); the parser depends on the schema.
    from jig.po_ontology_mcp import parse_ontology_md
    return parse_ontology_md(src.read_text())


def save_ontology(
    project_root: Path,
    ontology: Ontology,
    *,
    project_name: str = "Project",
) -> None:
    """Atomically render ``ontology`` to markdown at ``ontology.md``.

    ``project_name`` shows in the H1 heading; defaults to a generic
    placeholder so callers without a project name (early bootstrap)
    can still write a valid file.
    """
    from jig.po_ontology_mcp import render_ontology_md
    md = render_ontology_md(ontology, project_name=project_name)
    atomic_write_text(ontology_path(project_root), md)


# ---- v2 VD paths (Track D MVP) -------------------------------------------


def frontend_spec_path(project_root: Path) -> Path:
    """``.jig/spec/frontend.yaml`` — VD's top-level frontend declaration.

    Per ``docs/visual-design/design.md`` §"Frontend architecture (VD
    owns this)": one file per project, set once at VD discovery start
    (defaults applied immediately so a freshly-initialized project has
    a valid spec without operator interaction), modified rarely.
    """
    return project_root / _FRONTEND_SPEC_RELATIVE


def load_frontend_spec(project_root: Path) -> FrontendSpec:
    """Load and validate ``frontend.yaml``.

    Raises ``FileNotFoundError`` if absent — callers (the VD agent on
    re-spawn, the per-ticket reference resolver) treat absence as "VD
    hasn't run yet" rather than silently defaulting to the minimal
    stack. The defaults live on the schema; the absent-on-disk case is
    a real signal.
    """
    src = frontend_spec_path(project_root)
    if not src.is_file():
        raise FileNotFoundError(f"frontend spec not found at {src}")
    data = yaml.safe_load(src.read_text()) or {}
    return FrontendSpec.model_validate(data)


def save_frontend_spec(project_root: Path, spec: FrontendSpec) -> None:
    """Atomically write ``spec`` to ``.jig/spec/frontend.yaml``.

    ``sort_keys=False`` mirrors the other v2 writers so the operator
    inspecting the file mid-VD-walk sees a stable field order across
    saves. The parent dir is created on demand so the first save
    against a fresh project doesn't fail on a missing ``.jig/spec/``.
    """
    payload = yaml.safe_dump(spec.model_dump(mode="json"), sort_keys=False)
    atomic_write_text(frontend_spec_path(project_root), payload)


def system_dir(project_root: Path) -> Path:
    """``.jig/spec/system/`` — the VD design-system artifact dir.

    Holds ``tokens.yaml``, ``components.yaml``, and ``brand.yaml``.
    Per design.md the design system is always present — when the dir
    or any of the three artifacts is absent, the loaders below return
    the shipped defaults rather than raising. This matches the
    "default is a permanent valid state" rule.
    """
    return project_root / ".jig" / "spec" / "system"


def tokens_path(project_root: Path) -> Path:
    """``.jig/spec/system/tokens.yaml`` — design-token list."""
    return system_dir(project_root) / "tokens.yaml"


def components_path(project_root: Path) -> Path:
    """``.jig/spec/system/components.yaml`` — component library."""
    return system_dir(project_root) / "components.yaml"


def brand_path(project_root: Path) -> Path:
    """``.jig/spec/system/brand.yaml`` — brand voice + tone + logo refs."""
    return system_dir(project_root) / "brand.yaml"


def load_tokens(project_root: Path) -> Tokens:
    """Load ``tokens.yaml`` or return the shipped default set.

    Absent file returns ``DEFAULT_TOKENS`` rather than raising so the
    "default is a permanent valid state" rule from design.md holds at
    the loader boundary. Callers that need to distinguish "operator
    customized" from "running on defaults" check ``tokens.source``.
    """
    src = tokens_path(project_root)
    if not src.is_file():
        return DEFAULT_TOKENS
    data = yaml.safe_load(src.read_text()) or {}
    return Tokens.model_validate(data)


def load_components(project_root: Path) -> ComponentLibrary:
    """Load ``components.yaml`` or return the shipped default set."""
    src = components_path(project_root)
    if not src.is_file():
        return DEFAULT_COMPONENTS
    data = yaml.safe_load(src.read_text()) or {}
    return ComponentLibrary.model_validate(data)


def load_brand(project_root: Path) -> Brand:
    """Load ``brand.yaml`` or return the shipped default brand."""
    src = brand_path(project_root)
    if not src.is_file():
        return DEFAULT_BRAND
    data = yaml.safe_load(src.read_text()) or {}
    return Brand.model_validate(data)


def load_design_system(project_root: Path) -> DesignSystem:
    """Load the full design system (tokens + components + brand).

    Convenience for callers that want all three at once (per-ticket
    reference resolver, visual_compliance reviewer). Returns
    ``DEFAULT_DESIGN_SYSTEM`` when none of the three files exist; mixed
    states (one file present, two absent) build the aggregate from
    whatever's on disk + defaults for the rest.
    """
    if not system_dir(project_root).is_dir():
        return DEFAULT_DESIGN_SYSTEM
    return DesignSystem(
        tokens=load_tokens(project_root),
        components=load_components(project_root),
        brand=load_brand(project_root),
    )


def save_tokens(project_root: Path, tokens: Tokens) -> None:
    """Atomically write ``tokens.yaml``."""
    payload = yaml.safe_dump(tokens.model_dump(mode="json"), sort_keys=False)
    atomic_write_text(tokens_path(project_root), payload)


def save_components(project_root: Path, components: ComponentLibrary) -> None:
    """Atomically write ``components.yaml``."""
    payload = yaml.safe_dump(components.model_dump(mode="json"), sort_keys=False)
    atomic_write_text(components_path(project_root), payload)


def save_brand(project_root: Path, brand: Brand) -> None:
    """Atomically write ``brand.yaml``."""
    payload = yaml.safe_dump(brand.model_dump(mode="json"), sort_keys=False)
    atomic_write_text(brand_path(project_root), payload)


def wireframes_dir(project_root: Path) -> Path:
    """``.jig/spec/wireframes/`` — VD's per-screen HTML wireframes dir."""
    return project_root / ".jig" / "spec" / "wireframes"


def wireframe_path(project_root: Path, screen_id: str) -> Path:
    """``.jig/spec/wireframes/<screen_id>.html`` — one per screen."""
    return wireframes_dir(project_root) / f"{screen_id}.html"


def wireframe_css_path(project_root: Path) -> Path:
    """``.jig/spec/wireframes/wireframe.css`` — the shared utility layer."""
    return wireframes_dir(project_root) / "wireframe.css"


def wireframe_notes_path(project_root: Path, screen_id: str) -> Path:
    """``.jig/spec/wireframes/<screen_id>.notes.md`` — operator notes per screen.

    Sidecar markdown carrying interaction notes, state descriptions,
    and cross-references — separate from the structural HTML so the
    operator can hand-edit prose without touching markup. Per
    design.md §"Per-screen notes (sidecar markdown, retained)".
    """
    return wireframes_dir(project_root) / f"{screen_id}.notes.md"


def load_wireframe(project_root: Path, screen_id: str) -> str:
    """Load a wireframe's HTML content; raise ``FileNotFoundError`` if absent."""
    src = wireframe_path(project_root, screen_id)
    if not src.is_file():
        raise FileNotFoundError(f"wireframe {screen_id!r} not found at {src}")
    return src.read_text()


def save_wireframe(project_root: Path, screen_id: str, html: str) -> None:
    """Atomically write a wireframe's HTML content.

    The parent dir is created on demand. ``html`` is written verbatim
    (no schema enforcement here — the linter does that at the MCP tool
    boundary so the caller sees structured violations rather than a
    file-write rejection).
    """
    atomic_write_text(wireframe_path(project_root, screen_id), html)
