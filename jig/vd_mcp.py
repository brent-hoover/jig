"""VD MCP tool handlers (Track D MVP).

Per ``docs/visual-design/design.md`` §"MCP tool surface for VD agents":
the VD agent edits HTML directly via Read/Write/Edit; the MCP surface
provides thin wrappers for incremental upserts (wireframes, tokens,
components), the linter, the per-screen notes, the finalize handoff,
and a stub Claude Design import path.

Authoring tools:

- ``vd_set_wireframe(screen_id, html, meta)`` — incremental upsert of
  a per-screen HTML wireframe; runs the linter and raises on critical
  errors before write.
- ``vd_set_design_token(kind, id, value)`` — incremental upsert into
  ``tokens.yaml``.
- ``vd_set_component(id, name, variants)`` — incremental upsert into
  ``components.yaml``.
- ``vd_finalize(frontend, wireframes, ...)`` — atomically write the
  full VD payload (frontend.yaml + per-screen HTML) and post the
  Handoff to PM.

Read / utility tools:

- ``wireframe_lint(html)`` — exposes the deterministic linter so the
  agent can pre-flight HTML before saving.
- ``wireframe_get_notes(screen_id)`` / ``wireframe_set_notes`` —
  operator-readable sidecar markdown notes per wireframe.

Import:

- ``vd_import_claude_design(payload)`` — stub that takes a payload of
  the shape ``{tokens, components, wireframes}`` and writes them in
  jig's format. Real Claude Design integration is a separate operator
  concern; the import path exists so an operator who has Claude Design
  output can land it without hand-merging.

The handlers mirror ``jig.sa_incremental_mcp`` in style: idempotent
upsert primitives keyed on natural id, finalize handler writes atomically
+ posts Handoff + resolves the ticket via the shared helper. No LLM
calls — every handler is deterministic.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError

from jig.atomic import atomic_write_text
from jig.handoff_resolve import resolve_after_handoff
from jig.intent import ComplicationsConsidered, Intent
from jig.schemas.design_system import (
    Brand,
    Component,
    ComponentLibrary,
    ComponentVariant,
    DesignToken,
    Tokens,
)
from jig.schemas.frontend import FrontendSpec
from jig.spec_loader import (
    load_components,
    load_tokens,
    save_brand,
    save_components,
    save_frontend_spec,
    save_tokens,
    save_wireframe,
    wireframe_notes_path,
)
from jig.store.bus import Message, MessageBus, MessageType
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.thread import Handoff
from jig.wireframes.format import WireframeMeta, render_meta_comment
from jig.wireframes.linter import LintSeverity, lint_wireframe

__all__ = [
    "VD_NEXT_PHASE",
    "VD_TICKET_ID",
    "handle_vd_finalize",
    "handle_vd_import_claude_design",
    "handle_vd_set_component",
    "handle_vd_set_design_token",
    "handle_vd_set_wireframe",
    "handle_wireframe_get_notes",
    "handle_wireframe_lint",
    "handle_wireframe_set_notes",
]


# Deterministic ticket id the synthetic operator + scenario YAML use to
# create the VD-finalize ticket. Mirrors ``SA_TICKET_ID`` /
# ``L1_TICKET_ID`` — a single discoverable id per role-finalize so
# resolve_after_handoff has something concrete to flip.
VD_TICKET_ID = "frontend"
# Phase the Handoff routes to. PM is the next stop in the v2 lifecycle
# per docs/visual-design/design.md §"Sequencing relative to PO / SA / PM".
VD_NEXT_PHASE = "pm"


# ---- shared helpers -------------------------------------------------------


def _coerce(model_cls: type, raw: Any, *, kind: str) -> Any:
    """Coerce a dict (or instance) into a Pydantic model.

    Re-raises ``ValidationError`` as ``ValueError`` so the MCP error
    surface is uniform across all upsert handlers — agents see one
    error type rather than two depending on whether the failure was at
    coerce time or schema time. Mirrors ``sa_incremental_mcp._coerce``.
    """
    if isinstance(raw, model_cls):
        return raw
    if not isinstance(raw, dict):
        raise ValueError(
            f"{kind} must be a dict or {model_cls.__name__}, got "
            f"{type(raw).__name__}"
        )
    try:
        return model_cls.model_validate(raw)
    except ValidationError as e:
        raise ValueError(f"{kind} does not validate: {e}") from e


def _replace_or_append(items: list, new_item: Any, *, id_field: str = "id") -> list:
    """Idempotent upsert primitive — replace by id or append.

    Returns a new list rather than mutating in place so callers can't
    accidentally reuse a mutable reference across the load + save
    round-trip. Mirrors ``sa_incremental_mcp._replace_or_append``.
    """
    new_id = getattr(new_item, id_field)
    return [item for item in items if getattr(item, id_field) != new_id] + [
        new_item
    ]


def _critical_lint_failures(html: str) -> list[str]:
    """Return critical lint messages for ``html``; empty list = clean enough.

    The linter returns three severities; only ``CRITICAL`` blocks the
    upsert (missing meta, inline styles, disallowed scripts). Important
    / notable findings get returned as part of the upsert response so
    the agent sees them but they don't gate persistence — the design's
    "iterate fast" rule.
    """
    return [
        e.message
        for e in lint_wireframe(html)
        if e.severity == LintSeverity.CRITICAL.value
    ]


# ---- wireframe upserts ---------------------------------------------------


async def handle_vd_set_wireframe(
    *,
    project_path: Path,
    screen_id: str,
    html: str,
    meta: Any | None = None,
) -> dict[str, Any]:
    """Upsert one wireframe HTML file under ``.jig/spec/wireframes/``.

    Runs the linter on ``html`` and raises ``ValueError`` if any
    critical violations are present. ``meta`` is an optional override:
    when provided, the handler splices a fresh meta comment in front
    of the HTML (replacing any existing meta) so the agent doesn't
    have to re-author the comment by hand. When omitted, the handler
    requires the HTML to already carry its own meta block (the linter
    enforces presence).

    Returns ``{screen_id, warnings}`` so the agent sees non-critical
    findings even when the upsert succeeds. Mirrors
    ``handle_module_set_behavioral_contract``'s shape.
    """
    if meta is not None:
        meta_obj = _coerce(WireframeMeta, meta, kind="wireframe meta")
        # Splice: replace any existing wireframe-meta comment with the
        # fresh one; if none present, prepend. Done as a string op
        # (not a parser) because the linter treats meta as a leading
        # comment, not arbitrary inner-document markup.
        from jig.wireframes.format import META_COMMENT_RE

        rendered = render_meta_comment(meta_obj)
        if META_COMMENT_RE.search(html):
            html = META_COMMENT_RE.sub(rendered, html, count=1)
        else:
            html = rendered + "\n" + html

    critical = _critical_lint_failures(html)
    if critical:
        raise ValueError(
            f"vd_set_wireframe: {len(critical)} critical lint error(s) — "
            f"{critical!r}. Fix before re-saving."
        )

    save_wireframe(project_path, screen_id, html)
    # Capture remaining (non-critical) lint findings so the agent can
    # see them even though the upsert succeeded. Bones MVP returns the
    # whole serialized list rather than a structured map; the agent
    # treats it as advisory.
    advisories = [
        e.message
        for e in lint_wireframe(html)
        if e.severity != LintSeverity.CRITICAL.value
    ]
    return {"screen_id": screen_id, "warnings": advisories}


async def handle_wireframe_lint(*, html: str) -> list[dict[str, Any]]:
    """Expose the linter as an MCP tool — pre-flight wireframe HTML.

    Returns a list of {code, severity, message, line} dicts so the
    agent's response is JSON-friendly (the LintError model carries the
    same fields but uses string-enum coercion).
    """
    return [e.model_dump(mode="json") for e in lint_wireframe(html)]


# ---- per-screen notes (sidecar markdown) ---------------------------------


async def handle_wireframe_get_notes(
    *, project_path: Path, screen_id: str
) -> str:
    """Read the per-screen sidecar notes markdown.

    Returns the empty string (rather than raising) when the notes file
    is absent. Notes are operator-readable; the absent-state is "no
    notes yet", not an error.
    """
    src = wireframe_notes_path(project_path, screen_id)
    if not src.is_file():
        return ""
    return src.read_text()


async def handle_wireframe_set_notes(
    *, project_path: Path, screen_id: str, notes: str
) -> str:
    """Write the per-screen sidecar notes markdown atomically.

    No schema enforcement on notes content — it's free-form markdown
    the operator may hand-edit. Returns the on-disk path (relative to
    the project root) so the agent can echo the location to the
    operator.
    """
    target = wireframe_notes_path(project_path, screen_id)
    atomic_write_text(target, notes)
    return str(target.relative_to(project_path))


# ---- design system upserts -----------------------------------------------


async def handle_vd_set_design_token(
    *, project_path: Path, token: Any
) -> str:
    """Upsert one DesignToken into ``tokens.yaml``.

    Loads the on-disk tokens file (or starts from the shipped defaults
    if absent — that's the right starting state per design.md), upserts
    by id, writes back. Returns the token id for the agent to use in
    the response stream.
    """
    t = _coerce(DesignToken, token, kind="design_token")
    tokens = load_tokens(project_path)
    # Flip the source to operator_supplied the first time the operator
    # touches the token set — defaults stop being authoritative once
    # the operator has authored anything.
    new_source = (
        "operator_supplied"
        if tokens.source == "default"
        else tokens.source
    )
    updated = Tokens(
        spec_version=tokens.spec_version,
        source=new_source,
        tokens=_replace_or_append(tokens.tokens, t),
    )
    save_tokens(project_path, updated)
    return t.id


async def handle_vd_set_component(
    *, project_path: Path, component: Any
) -> str:
    """Upsert one Component into ``components.yaml``.

    Mirrors ``handle_vd_set_design_token`` — load (with default
    fallback), upsert by id, save. Variants are accepted as a list of
    dicts or ComponentVariant instances; the coercion happens via the
    Component model's own validation.
    """
    c = _coerce(Component, component, kind="component")
    library = load_components(project_path)
    new_source = (
        "operator_supplied"
        if library.source == "default"
        else library.source
    )
    updated = ComponentLibrary(
        spec_version=library.spec_version,
        source=new_source,
        components=_replace_or_append(library.components, c),
    )
    save_components(project_path, updated)
    return c.id


# ---- vd_finalize ---------------------------------------------------------


async def handle_vd_finalize(
    *,
    tickets: TicketStore,
    threads: ThreadStore,
    bus: MessageBus,
    project_path: Path,
    frontend: Any,
    wireframes: list[dict[str, Any]] | None = None,
    summary: str,
    author: str,
) -> str:
    """Finalize the VD payload — write artifacts, post Handoff, resolve.

    Steps:

    1. Coerce + write ``frontend.yaml``.
    2. For each entry in ``wireframes`` (a list of
       ``{screen_id, html, meta?}``), run the linter; raise if any
       carry critical errors so a partial write doesn't leave the
       project in a half-saved state.
    3. Write each wireframe HTML atomically.
    4. Post the Handoff (phase=PM) and resolve the VD ticket via the
       shared helper.

    The wireframes list is optional — backend-only projects pass an
    empty list and VD-finalize is the "VD has nothing to do" exit per
    design.md.
    """
    frontend_spec = _coerce(FrontendSpec, frontend, kind="frontend_spec")

    wireframes = wireframes or []

    # Linter pre-flight across ALL wireframes BEFORE writing any. This
    # keeps the operation atomic from the agent's perspective: either
    # every wireframe lands or none does. Without the upfront pass we
    # could write 5 wireframes, fail on the 6th, and leave the project
    # in a half-finalized state the agent has to clean up.
    rendered: list[tuple[str, str]] = []
    for entry in wireframes:
        screen_id = entry.get("screen_id")
        html = entry.get("html")
        meta_raw = entry.get("meta")
        if not screen_id or not html:
            raise ValueError(
                f"vd_finalize: each wireframe entry needs screen_id + html; "
                f"got {entry!r}"
            )
        if meta_raw is not None:
            meta_obj = _coerce(WireframeMeta, meta_raw, kind="wireframe meta")
            from jig.wireframes.format import META_COMMENT_RE

            rendered_meta = render_meta_comment(meta_obj)
            if META_COMMENT_RE.search(html):
                html = META_COMMENT_RE.sub(rendered_meta, html, count=1)
            else:
                html = rendered_meta + "\n" + html
        critical = _critical_lint_failures(html)
        if critical:
            raise ValueError(
                f"vd_finalize: wireframe {screen_id!r} has "
                f"{len(critical)} critical lint error(s) — {critical!r}"
            )
        rendered.append((screen_id, html))

    # All-clean — write everything.
    save_frontend_spec(project_path, frontend_spec)
    for screen_id, html in rendered:
        save_wireframe(project_path, screen_id, html)

    handoff = Handoff(
        ticket_id=VD_TICKET_ID,
        author=author,
        phase=VD_NEXT_PHASE,
        outputs=[
            ".jig/spec/frontend.yaml",
            *[
                f".jig/spec/wireframes/{sid}.html"
                for sid, _ in rendered
            ],
        ],
        summary=summary,
    )
    entry_id = await threads.post(handoff)
    await bus.publish(
        Message(
            sender=author,
            to="orchestrator",
            type=MessageType.CONTEXT_UPDATE,
            payload={
                "kind": "handoff_posted",
                "ticket_id": VD_TICKET_ID,
                "phase": VD_NEXT_PHASE,
                "wireframe_count": len(rendered),
            },
            topic="orchestrator",
        )
    )
    await resolve_after_handoff(
        tickets=tickets,
        threads=threads,
        bus=bus,
        ticket_id=VD_TICKET_ID,
        author=author,
    )
    return entry_id


# ---- Claude Design import (stub) ------------------------------------------


def _default_intent_for_import() -> Intent:
    """Synthesize an Intent for an imported FrontendSpec.

    Claude Design imports don't carry an intent layer — the operator
    authors the design system upstream and exports tokens. We
    synthesize a minimal intent so the FrontendSpec stays valid; the
    operator can later overwrite via the regular VD flow.
    """
    return Intent(
        problem="Frontend spec was imported from Claude Design output rather than authored in jig.",
        simplest_solution="Take the import as-is and let the operator iterate via vd_set_* tools.",
        complications_considered=ComplicationsConsidered(),
    )


async def handle_vd_import_claude_design(
    *, project_path: Path, payload: dict[str, Any]
) -> dict[str, Any]:
    """Import a Claude Design payload — stub for MVP.

    Real Claude Design integration is per-Anthropic product release;
    for MVP we accept a small JSON payload of the shape::

        {
          "tokens": [{id, kind, value}, ...],
          "components": [{id, name, variants?}, ...],
          "brand": {voice?, tone?, logo_refs?},
          "wireframes": [{screen_id, html, meta?}, ...]
        }

    Each section is optional. The handler maps each into jig's schemas
    and writes via the same save_* helpers the incremental upserts
    use. Returns a summary dict the agent can echo to the operator.
    """
    counts: dict[str, int] = {}

    tokens_payload = payload.get("tokens")
    if tokens_payload:
        token_objs = [
            _coerce(DesignToken, t, kind="design_token") for t in tokens_payload
        ]
        save_tokens(
            project_path,
            Tokens(source="claude_design", tokens=token_objs),
        )
        counts["tokens"] = len(token_objs)

    components_payload = payload.get("components")
    if components_payload:
        component_objs: list[Component] = []
        for c in components_payload:
            if isinstance(c, Component):
                component_objs.append(c)
                continue
            if not isinstance(c, dict):
                raise ValueError(
                    f"component must be a dict or Component, got {type(c).__name__}"
                )
            variants_raw = c.get("variants") or []
            variants = [
                _coerce(ComponentVariant, v, kind="component_variant")
                for v in variants_raw
            ]
            component_objs.append(
                Component(
                    id=c["id"],
                    name=c["name"],
                    description=c.get("description"),
                    variants=variants,
                )
            )
        save_components(
            project_path,
            ComponentLibrary(source="claude_design", components=component_objs),
        )
        counts["components"] = len(component_objs)

    brand_payload = payload.get("brand")
    if brand_payload:
        save_brand(
            project_path,
            Brand(
                source="claude_design",
                voice=brand_payload.get("voice", Brand().voice),
                tone=brand_payload.get("tone", Brand().tone),
                logo_refs=brand_payload.get("logo_refs", []),
            ),
        )
        counts["brand"] = 1

    wireframes_payload = payload.get("wireframes") or []
    for entry in wireframes_payload:
        screen_id = entry["screen_id"]
        html = entry["html"]
        meta_raw = entry.get("meta")
        if meta_raw is not None:
            meta_obj = _coerce(WireframeMeta, meta_raw, kind="wireframe meta")
            from jig.wireframes.format import META_COMMENT_RE

            rendered = render_meta_comment(meta_obj)
            if META_COMMENT_RE.search(html):
                html = META_COMMENT_RE.sub(rendered, html, count=1)
            else:
                html = rendered + "\n" + html
        critical = _critical_lint_failures(html)
        if critical:
            raise ValueError(
                f"imported wireframe {screen_id!r} has critical lint errors: {critical!r}"
            )
        save_wireframe(project_path, screen_id, html)
    if wireframes_payload:
        counts["wireframes"] = len(wireframes_payload)

    return {
        "imported": counts,
        "source": "claude_design",
    }
