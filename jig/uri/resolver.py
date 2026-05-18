"""Multi-authority dispatcher for ``project://`` URIs.

Two paths exist:

- ``resolve_project_uri(uri, project_root)`` — full dispatcher; loads the
  matching artifact from disk based on authority. Spec authority is wired
  for v2 bones; others raise ``UnimplementedAuthorityError`` until their
  owning tracks land.
- ``resolve_spec_uri(uri, spec)`` — re-exported from ``jig.uri.spec``; takes
  a pre-loaded ``StructuredSpec`` (preserves the v1 caller pattern).

See ``docs/v2.0/uri-scheme/design.md`` §"Resolution mechanics".
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jig.uri.arch import resolve_arch_uri
from jig.uri.design import resolve_design_uri
from jig.uri.errors import ProjectUriError
from jig.uri.parser import ProjectUri, parse_project_uri
from jig.uri.plan import resolve_plan_uri
from jig.uri.store import resolve_store_uri

if TYPE_CHECKING:
    from jig.uri.cache import UriResolverCache


@dataclass(frozen=True)
class ResolvedUri:
    """Result of a successful URI resolution."""

    kind: str
    data: Any
    source_path: Path | None
    revision: int | None


def resolve_project_uri(
    uri: str | ProjectUri,
    project_root: Path,
    *,
    cache: "UriResolverCache | None" = None,
) -> ResolvedUri:
    """Resolve a project URI by dispatching to the per-authority resolver.

    Loads the matching artifact from ``project_root`` and applies the URI
    fragment. Spec authority is the only resolver wired up for bones — it
    loads ``.jig/spec/spec.structured.yaml`` and dispatches into the existing
    spec resolver.

    ``cache`` is opt-in; when provided we consult it before dispatching and
    populate it on miss. ``None`` (default) keeps every call a fresh load —
    backward-compatible for callers that haven't adopted caching yet.
    """
    parsed = parse_project_uri(uri) if isinstance(uri, str) else uri

    if cache is not None:
        cached = cache.get(parsed)
        if cached is not None:
            return cached

    if parsed.authority == "spec":
        result = _resolve_spec_authority(parsed, project_root)
    elif parsed.authority == "arch":
        data = resolve_arch_uri(parsed, project_root)
        result = ResolvedUri(
            kind="arch", data=data, source_path=None, revision=parsed.revision
        )
    elif parsed.authority == "design":
        data = resolve_design_uri(parsed, project_root)
        result = ResolvedUri(
            kind="design", data=data, source_path=None, revision=parsed.revision
        )
    elif parsed.authority == "plan":
        data = resolve_plan_uri(parsed, project_root)
        result = ResolvedUri(
            kind="plan", data=data, source_path=None, revision=parsed.revision
        )
    elif parsed.authority == "store":
        data = resolve_store_uri(parsed, project_root)
        result = ResolvedUri(
            kind="store", data=data, source_path=None, revision=parsed.revision
        )
    else:
        raise ProjectUriError(f"unhandled authority {parsed.authority!r}")

    if cache is not None:
        cache.put(parsed, result)
    return result


def _resolve_spec_authority(parsed: ProjectUri, project_root: Path) -> ResolvedUri:
    from jig.spec_loader import load_structured_spec
    from jig.uri.spec import resolve_spec_uri

    spec, source_path = load_structured_spec(project_root)
    out = resolve_spec_uri(parsed, spec)
    return ResolvedUri(
        kind=out["kind"],
        data=out["data"],
        source_path=source_path,
        revision=parsed.revision,
    )
