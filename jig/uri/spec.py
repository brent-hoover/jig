"""Resolver for ``project://spec/...`` URIs.

Resolves against an in-memory ``StructuredSpec`` (the v1 spec format —
extension to multi-level v2 spec lands in Track B). Returns a ``{kind, data}``
dict.

API preserved from the v1 ``jig/spec_uri.py`` so existing callers remain
unchanged. Built on the new multi-authority parser in ``jig.uri.parser``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from jig.uri.errors import ProjectUriError
from jig.uri.parser import ProjectUri, parse_project_uri

if TYPE_CHECKING:
    from jig.spec_schema import StructuredSpec


def resolve_spec_uri(uri: str | ProjectUri, spec: "StructuredSpec") -> dict[str, Any]:
    """Resolve a ``project://spec/...`` URI against the given spec.

    Accepts either a raw URI string or a pre-parsed ``ProjectUri``.

    Returns a ``{kind, data}`` dict where ``kind`` is one of ``spec |
    capability | behavior | non_goal | capability_list | non_goal_list |
    name | summary``. ``data`` is a model_dump'd dict for objects, raw value
    for primitives.

    Raises:
        ProjectUriError: malformed URI, unknown ids, unknown fragments,
            unknown state collection, or revision pinning (not yet supported
            for the v1 spec format).
    """
    from jig.spec_schema import CapabilityState

    parsed = parse_project_uri(uri) if isinstance(uri, str) else uri
    if parsed.authority != "spec":
        raise ProjectUriError(
            f"resolve_spec_uri called with non-spec URI: authority {parsed.authority!r}"
        )
    if parsed.revision is not None:
        raise ProjectUriError("revision pinning not yet supported by the spec resolver")

    parts = parsed.path

    if not parts:
        return {"kind": "spec", "data": spec.model_dump(mode="json", by_alias=True)}

    head = parts[0]

    if head == "name" and len(parts) == 1:
        return {"kind": "name", "data": spec.name}

    if head == "summary" and len(parts) == 1:
        return {"kind": "summary", "data": spec.summary}

    if head == "capabilities":
        return _resolve_capabilities(parts, parsed.fragment, spec)

    if head == "non-goals":
        return _resolve_non_goals(parts, spec)

    if head == "state":
        return _resolve_state(parts, spec, CapabilityState)

    raise ProjectUriError(f"unrecognized spec URI path: {list(parts)!r}")


def _resolve_capabilities(
    parts: tuple[str, ...], fragment: str | None, spec: "StructuredSpec"
) -> dict[str, Any]:
    if len(parts) == 1:
        return {
            "kind": "capability_list",
            "data": [
                c.model_dump(mode="json", by_alias=True) for c in spec.capabilities
            ],
        }
    cap_id = parts[1]
    cap = spec.capability_by_id_or_alias(cap_id)
    if cap is None:
        raise ProjectUriError(f"capability {cap_id!r} not found in spec")
    if fragment is None:
        return {
            "kind": "capability",
            "data": cap.model_dump(mode="json", by_alias=True),
        }
    for b in cap.behaviors:
        if b.id == fragment:
            return {"kind": "behavior", "data": b.model_dump(mode="json")}
    raise ProjectUriError(f"behavior {fragment!r} not found in capability {cap_id!r}")


def _resolve_non_goals(
    parts: tuple[str, ...], spec: "StructuredSpec"
) -> dict[str, Any]:
    if len(parts) == 1:
        return {
            "kind": "non_goal_list",
            "data": [n.model_dump(mode="json") for n in spec.non_goals],
        }
    ng_id = parts[1]
    ng = spec.non_goal_by_id_or_alias(ng_id)
    if ng is None:
        raise ProjectUriError(f"non-goal {ng_id!r} not found in spec")
    return {"kind": "non_goal", "data": ng.model_dump(mode="json")}


def _resolve_state(
    parts: tuple[str, ...], spec: "StructuredSpec", state_enum: type
) -> dict[str, Any]:
    if len(parts) != 2:
        raise ProjectUriError("state URI requires a single state name segment")
    try:
        state = state_enum(parts[1])
    except ValueError as exc:
        raise ProjectUriError(
            f"unknown state {parts[1]!r}; expected one of "
            f"{sorted(s.value for s in state_enum)}"
        ) from exc
    return {
        "kind": "capability_list",
        "data": [
            c.model_dump(mode="json", by_alias=True)
            for c in spec.capabilities
            if c.state == state
        ],
    }
