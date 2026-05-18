"""Wireframe metadata schema + extraction from HTML comments.

Per ``docs/v2.0/visual-design/design.md`` §"Wireframe → bones continuity":
each ``<screen-id>.html`` carries metadata in a leading HTML comment so
the linker / index generator can build the screen roster without
parsing the body. Format::

    <!-- wireframe-meta: {"screen_id": "post-a-job", "title": "Post a job",
         "persona_targets": ["merchant"], "journey_refs": ["j-merchant-onboarding"],
         "notes": "..."} -->

JSON inside the comment because YAML inside an HTML comment is fragile
(indentation matters, multi-line is awkward); JSON has the same
expressive power for our flat shape and round-trips through the existing
stdlib parser.
"""

from __future__ import annotations

import json
import re

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "META_COMMENT_RE",
    "WireframeMeta",
    "extract_meta",
    "render_meta_comment",
]


# Regex picks the **first** ``<!-- wireframe-meta: ... -->`` comment in
# a document. ``re.DOTALL`` lets the JSON span lines (the renderer keeps
# it on one line, but operators may hand-edit and reflow). The strict
# ``wireframe-meta:`` prefix means a comment that just *mentions* the
# string in prose (e.g. inside another comment block) doesn't get
# accidentally matched.
META_COMMENT_RE = re.compile(
    r"<!--\s*wireframe-meta:\s*(?P<json>\{.*?\})\s*-->",
    re.DOTALL,
)


class WireframeMeta(BaseModel):
    """Per-screen wireframe metadata.

    All five fields are required by the design's checklist (every
    wireframe ties back to at least one persona + journey for the
    coverage check). For MVP scope ``persona_targets`` and
    ``journey_refs`` accept empty lists so the agent can author a
    skeleton wireframe and fill in the references on a follow-on call —
    the linker treats empties as a soft warning, not a hard reject.

    Track D Final adds ``breakpoints`` so the responsive-design
    reviewer knows which viewports the wireframe targets (typically
    some subset of mobile / tablet / desktop). Default empty for
    backwards compatibility with MVP-era wireframes; the responsive
    reviewer flags an empty list with an IMPORTANT comment so
    operators can backfill.
    """

    model_config = ConfigDict(extra="forbid")

    screen_id: str = Field(..., min_length=1, description="kebab-case screen id")
    title: str = Field(..., min_length=1)
    persona_targets: list[str] = Field(default_factory=list)
    journey_refs: list[str] = Field(default_factory=list)
    notes: str | None = None
    breakpoints: list[str] = Field(
        default_factory=list,
        description=(
            "Target breakpoints the wireframe is designed for "
            "(typically a subset of 'mobile' / 'tablet' / 'desktop'). "
            "Empty list is permitted so existing wireframes load "
            "without re-authoring; the responsive-design reviewer "
            "flags empty lists as a violation."
        ),
    )


def extract_meta(html: str) -> WireframeMeta | None:
    """Pull the ``WireframeMeta`` out of the leading meta comment, or None.

    Returns ``None`` (rather than raising) when no meta comment is
    present so the linter can choose to flag absence with a structured
    error rather than the caller getting a stack trace. Raises
    ``ValueError`` only when the comment is present but its JSON
    payload is malformed or fails schema validation — that's a
    distinct failure mode the agent needs to fix at the source.
    """
    match = META_COMMENT_RE.search(html)
    if match is None:
        return None
    payload = match.group("json")
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"wireframe-meta comment contains malformed JSON: {exc}"
        ) from exc
    return WireframeMeta.model_validate(data)


def render_meta_comment(meta: WireframeMeta) -> str:
    """Render ``meta`` as an HTML comment ready to splice into a wireframe.

    Produces a single-line comment so the agent's author-then-format
    loop doesn't reflow into invalid JSON. ``ensure_ascii=False`` lets
    operator-supplied unicode in titles / notes round-trip without
    escape noise.
    """
    payload = json.dumps(
        meta.model_dump(mode="json", exclude_none=True),
        ensure_ascii=False,
        sort_keys=False,
    )
    return f"<!-- wireframe-meta: {payload} -->"
