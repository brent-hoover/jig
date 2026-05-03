"""Multi-authority project URI scheme.

Parses and dispatches ``project://<authority>/<path>[@revision:<n>][#<fragment>]``.
Authorities: spec, arch, design, plan, store. See ``docs/uri-scheme/design.md``.

For v2 bones, only the ``spec`` authority resolves; others parse cleanly but raise
``UnimplementedAuthorityError`` at resolve time. They are wired up as their owning
tracks (C/D/F) land artifact schemas.
"""
from __future__ import annotations

from jig.uri.errors import (
    ProjectUriError,
    UnimplementedAuthorityError,
    UnknownAuthorityError,
)
from jig.uri.parser import (
    ProjectUri,
    parse_project_uri,
)
from jig.uri.resolver import (
    ResolvedUri,
    resolve_project_uri,
)
from jig.uri.spec import resolve_spec_uri

__all__ = [
    "ProjectUri",
    "ProjectUriError",
    "ResolvedUri",
    "UnimplementedAuthorityError",
    "UnknownAuthorityError",
    "parse_project_uri",
    "resolve_project_uri",
    "resolve_spec_uri",
]
