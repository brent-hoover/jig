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

from jig.substrate import StoreAuthority
from jig.uri.parser import parse_project_uri


@dataclass(frozen=True)
class AuthoringEngine:
    """An authoring engine that owns one ``project://`` authority."""

    name: str
    authority: str

    def __post_init__(self) -> None:
        if self.authority not in StoreAuthority.AUTHORITIES:
            raise ValueError(
                f"unknown authority {self.authority!r} for engine {self.name!r}; "
                f"must be one of {StoreAuthority.AUTHORITIES}"
            )

    def owns(self, uri: str) -> bool:
        """Whether ``uri`` falls under this engine's authority."""
        return parse_project_uri(uri).authority == self.authority
