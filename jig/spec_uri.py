"""Parser for ``project://spec/...`` URIs.

Strict prefix — relative URIs are rejected. Empty fragment is treated
as no fragment.

See ``docs/project-spec-schema/design.md`` §"URI scheme".
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


_PREFIX = "project://spec"


class SpecUriError(ValueError):
    """Raised when a project://spec/... URI is malformed."""


@dataclass
class SpecUri:
    """Parsed URI: parts after the prefix, plus optional fragment."""
    parts: list[str]
    fragment: str | None


def parse_spec_uri(uri: str) -> SpecUri:
    """Parse a ``project://spec/...`` URI.

    Returns SpecUri(parts=[after-prefix-segments], fragment=...).
    Empty fragment → None. Raises SpecUriError if not a project://spec
    URI or the path is malformed.
    """
    if not uri.startswith(_PREFIX):
        raise SpecUriError(
            f"URI must start with {_PREFIX!r} prefix; got {uri!r}"
        )
    rest = uri[len(_PREFIX):]
    if rest and not rest.startswith("/") and not rest.startswith("#"):
        raise SpecUriError(
            f"expected '/' or '#' or end after {_PREFIX!r}; got {uri!r}"
        )
    fragment: str | None = None
    if "#" in rest:
        path_part, _, frag = rest.partition("#")
        rest = path_part
        if frag:
            fragment = frag
    rest = rest.lstrip("/")
    parts = [p for p in rest.split("/") if p]
    return SpecUri(parts=parts, fragment=fragment)


def resolve_spec_uri(uri: str, spec: "StructuredSpec") -> dict[str, Any]:  # noqa: F821
    """Resolve a ``project://spec/...`` URI against the given spec.

    Returns a ``{kind, data}`` dict where ``kind`` is one of
    ``spec / capability / behavior / non_goal / capability_list / non_goal_list / name / summary``
    and ``data`` is the structured value (model_dump'd dict for objects;
    raw value for primitives).

    Raises SpecUriError on unknown ids, unknown fragments, unknown
    state collections.
    """
    from jig.spec_schema import CapabilityState, StructuredSpec  # noqa: F401

    parsed = parse_spec_uri(uri)
    parts = parsed.parts

    if not parts:
        return {"kind": "spec", "data": spec.model_dump(mode="json", by_alias=True)}

    head = parts[0]

    if head == "name" and len(parts) == 1:
        return {"kind": "name", "data": spec.name}

    if head == "summary" and len(parts) == 1:
        return {"kind": "summary", "data": spec.summary}

    if head == "capabilities":
        if len(parts) == 1:
            return {
                "kind": "capability_list",
                "data": [c.model_dump(mode="json", by_alias=True)
                         for c in spec.capabilities],
            }
        cap_id = parts[1]
        cap = spec.capability_by_id_or_alias(cap_id)
        if cap is None:
            raise SpecUriError(
                f"capability {cap_id!r} not found in spec"
            )
        if parsed.fragment is None:
            return {
                "kind": "capability",
                "data": cap.model_dump(mode="json", by_alias=True),
            }
        for b in cap.behaviors:
            if b.id == parsed.fragment:
                return {
                    "kind": "behavior",
                    "data": b.model_dump(mode="json"),
                }
        raise SpecUriError(
            f"behavior {parsed.fragment!r} not found in capability {cap_id!r}"
        )

    if head == "non-goals":
        if len(parts) == 1:
            return {
                "kind": "non_goal_list",
                "data": [n.model_dump(mode="json") for n in spec.non_goals],
            }
        ng_id = parts[1]
        ng = spec.non_goal_by_id_or_alias(ng_id)
        if ng is None:
            raise SpecUriError(f"non-goal {ng_id!r} not found in spec")
        return {"kind": "non_goal", "data": ng.model_dump(mode="json")}

    if head == "state":
        if len(parts) != 2:
            raise SpecUriError(
                "state URI requires a single state name segment"
            )
        try:
            state = CapabilityState(parts[1])
        except ValueError:
            raise SpecUriError(
                f"unknown state {parts[1]!r}; expected one of "
                f"{sorted(s.value for s in CapabilityState)}"
            )
        return {
            "kind": "capability_list",
            "data": [c.model_dump(mode="json", by_alias=True)
                     for c in spec.capabilities if c.state == state],
        }

    raise SpecUriError(f"unrecognized spec URI path: {parts!r}")
