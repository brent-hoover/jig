"""Parser for ``project://<authority>/<path>[@revision:N][#<fragment>]``.

Pure string operations. No I/O. Validates syntax only — semantic resolution
(does the path actually exist?) happens in the per-authority resolvers.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from jig.uri.errors import ProjectUriError, UnknownAuthorityError

_PREFIX = "project://"
_KNOWN_AUTHORITIES = ("spec", "arch", "design", "plan", "store")
_SEGMENT_RE = re.compile(r"^[a-z0-9_-]+$")
_REVISION_RE = re.compile(r"^@revision:(\d+)$")

Authority = Literal["spec", "arch", "design", "plan", "store"]
FragmentStyle = Literal["anchor", "path", "none"]


@dataclass(frozen=True)
class ProjectUri:
    """Parsed ``project://<authority>/<path>[@revision:N][#<fragment>]``."""

    authority: Authority
    path: tuple[str, ...] = field(default_factory=tuple)
    revision: int | None = None
    fragment: str | None = None
    fragment_style: FragmentStyle = "none"


def parse_project_uri(uri: str) -> ProjectUri:
    """Parse a project:// URI into its components.

    Raises:
        ProjectUriError: when the URI is missing the prefix, has malformed
            segments, or has an invalid revision pin.
        UnknownAuthorityError: when the authority is not in the closed list.
    """
    if not uri.startswith(_PREFIX):
        raise ProjectUriError(
            f"URI must start with {_PREFIX!r} prefix; got {uri!r}"
        )

    rest = uri[len(_PREFIX):]
    if not rest:
        raise ProjectUriError(f"URI has no authority: {uri!r}")

    fragment, rest = _split_fragment(rest, uri)
    rest, revision = _split_revision(rest, uri)
    authority, path_segments = _split_authority_and_path(rest, uri)

    fragment_style = _classify_fragment(fragment)

    return ProjectUri(
        authority=authority,
        path=tuple(path_segments),
        revision=revision,
        fragment=fragment,
        fragment_style=fragment_style,
    )


def _split_fragment(rest: str, uri: str) -> tuple[str | None, str]:
    if "#" not in rest:
        return None, rest
    head, _, frag = rest.partition("#")
    if not frag:
        return None, head
    return frag, head


def _split_revision(rest: str, uri: str) -> tuple[str, int | None]:
    if "@" not in rest:
        return rest, None
    head, _, rev_part = rest.partition("@")
    match = _REVISION_RE.match(f"@{rev_part}")
    if not match:
        raise ProjectUriError(
            f"malformed revision pin in {uri!r}; expected '@revision:N'"
        )
    return head, int(match.group(1))


def _split_authority_and_path(
    rest: str, uri: str
) -> tuple[Authority, list[str]]:
    rest = rest.lstrip("/")
    if "/" in rest:
        authority_part, _, path_part = rest.partition("/")
    else:
        authority_part, path_part = rest, ""

    if authority_part not in _KNOWN_AUTHORITIES:
        raise UnknownAuthorityError(
            f"unknown authority {authority_part!r} in {uri!r}; "
            f"expected one of {_KNOWN_AUTHORITIES}"
        )

    segments = [s for s in path_part.split("/") if s]
    for seg in segments:
        if not _SEGMENT_RE.match(seg):
            raise ProjectUriError(
                f"malformed path segment {seg!r} in {uri!r}; "
                "segments must match [a-z0-9_-]+"
            )

    return authority_part, segments  # type: ignore[return-value]


def _classify_fragment(fragment: str | None) -> FragmentStyle:
    """Classify fragment style without I/O.

    A fragment containing '/' is path-style. A bare segment is anchor-style.
    The resolver may revisit this when it sees the source content type
    (markdown anchors vs YAML keys).
    """
    if fragment is None:
        return "none"
    if "/" in fragment:
        return "path"
    return "anchor"
