"""Segment validation helper shared by every constructor.

Constructors validate every operator-supplied path segment against the
URI grammar (kebab-case via the schema validator).  Keeping the call in
one helper keeps each constructor a single line.
"""

from __future__ import annotations

from jig.schemas._validators import validate_kebab_id


def seg(value: str, name: str) -> str:
    """Validate ``value`` is a kebab-case segment; return it unchanged."""
    return validate_kebab_id(value, name)


def revision_suffix(revision: int | None) -> str:
    """Render the ``@revision:N`` suffix or empty string when unpinned."""
    if revision is None:
        return ""
    if revision <= 0:
        raise ValueError(f"revision must be positive, got {revision!r}")
    return f"@revision:{revision}"


def fragment_suffix(fragment: str | None) -> str:
    """Render the ``#fragment`` suffix or empty string when none."""
    if fragment is None or fragment == "":
        return ""
    return f"#{fragment}"
