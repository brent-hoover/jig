"""Authoring engines — the shared authority-boundary contract.

The authoring engines (Discovery, Architecture, Visual Design) each own exactly
one ``project://`` authority and may only write there. ``AuthoringEngine`` makes
that boundary explicit and checkable: Discovery owns ``spec``, Architecture owns
``arch``, Visual Design owns ``design``. This is the structural invariant the
``ownership`` check (CORE Model) enforces — one boundary, one owner.

Bones defines the boundary + ``owns()`` routing; MVP routes the engines' writes
through their ``StoreAuthority`` and rejects cross-authority writes.
"""

from __future__ import annotations

from dataclasses import dataclass

from jig.uri.parser import parse_project_uri

# The authoring authorities — the artifact stores the engines author. ``plan``
# and ``store`` are not authored by an engine (PM/Build own those), so they are
# NOT valid here even though they are valid ``StoreAuthority`` authorities.
AUTHORING_AUTHORITIES: tuple[str, ...] = ("spec", "arch", "design")


@dataclass(frozen=True)
class AuthoringEngine:
    """An authoring engine that owns one ``project://`` authoring authority."""

    name: str
    authority: str

    def __post_init__(self) -> None:
        if self.authority not in AUTHORING_AUTHORITIES:
            raise ValueError(
                f"{self.authority!r} is not an authoring authority for engine "
                f"{self.name!r}; must be one of {AUTHORING_AUTHORITIES}"
            )

    def owns(self, uri: str) -> bool:
        """Whether ``uri`` falls under this engine's authority."""
        return parse_project_uri(uri).authority == self.authority
