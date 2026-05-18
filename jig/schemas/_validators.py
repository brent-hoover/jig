"""Shared field-shape validators for v2 Pydantic schemas.

Track A Final scope. These are *mechanical field-shape validators*, not
semantic validators — they enforce wire-format invariants (kebab-case ids,
URI-grammar refs, tz-aware datetimes) at schema load time so wrong-shape
inputs stop at the boundary.

Semantic validators (does this id exist in the spec?  is this URI's path
actually present?) live in the per-authority resolvers, not here.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal


# ---- service-kind taxonomy (TD-4) -----------------------------------------
#
# The data-store / manifest-service ``kind`` field is a small bounded
# vocabulary in practice — postgres, sqlite, nats, redis, s3, opensearch —
# but historically the field was ``str``. A shared ``Literal`` aliases the
# vocabulary across ``Architecture.data_stores[].kind`` and
# ``DevManifest.services[].kind`` so a typo in one fails to validate the
# other instead of silently drifting.
ServiceKind = Literal[
    "postgres",
    "sqlite",
    "mysql",
    "nats",
    "redis",
    "s3",
    "opensearch",
    "kafka",
    "other",
]

# ---- kebab-case ids -------------------------------------------------------
#
# v2 contract: ids are kebab-case (lowercase letters / digits / single
# hyphens between segments).  Underscore is reserved for path segments in
# the URI grammar — keeping ids hyphen-only stops the two from cross-
# contaminating downstream.  Free-text titles and descriptions are
# unrestricted; this rule applies only to id-shaped fields that propagate
# into URIs / file paths.

_KEBAB_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def validate_kebab_id(value: str, field: str) -> str:
    """Reject non-kebab ids.  Returns the value unchanged on success."""
    if not _KEBAB_RE.fullmatch(value):
        raise ValueError(
            f"{field} {value!r} must be kebab-case "
            "(lowercase letters, digits, single dashes between segments)"
        )
    return value


def validate_kebab_id_list(values: list[str], field: str) -> list[str]:
    """Apply ``validate_kebab_id`` to every entry in a list."""
    for v in values:
        validate_kebab_id(v, f"{field}[]")
    return values


# ---- project:// URI shape validators --------------------------------------
#
# Field-level URI validation is *shape only* — we want to reject "obviously
# wrong" strings (typos, missing prefix, bad authority) at load time without
# pulling in the resolver's I/O.  Full reference-integrity validation
# (does the URI actually resolve?) lives in arch_finalize / SA-side hooks.


def validate_project_uri_shape(value: str) -> str:
    """Validate that ``value`` is a syntactically well-formed ``project://`` URI.

    Delegates to ``parse_project_uri`` (pure string ops, no I/O).  Raises
    ``ValueError`` (Pydantic translates to ValidationError) on parse failure
    so the schema rejects mis-shaped URIs at field load.
    """
    # Late import — avoid pulling jig.uri at module-import time and creating
    # a cycle (schemas are imported by some uri stubs transitively).
    from jig.uri.errors import ProjectUriError
    from jig.uri.parser import parse_project_uri

    try:
        parse_project_uri(value)
    except ProjectUriError as exc:
        # Re-raise as ValueError so Pydantic packages it as a validation
        # error (Pydantic special-cases ValueError / TypeError / AssertionError).
        raise ValueError(str(exc)) from exc
    return value


# ---- tz-aware datetimes ---------------------------------------------------
#
# Pydantic v2 happily accepts naive datetimes for ``datetime`` fields.
# v2 artifacts must carry tz info (every default_factory uses
# ``datetime.now(timezone.utc)``); naive datetimes leaking in via
# operator-edited YAML / older fixtures cause downstream comparison bugs.
# Reject them at the schema layer.


def validate_tz_aware(value: datetime, field: str) -> datetime:
    """Reject naive ``datetime`` values (must carry tzinfo)."""
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(
            f"{field} must be timezone-aware; got naive datetime {value!r}"
        )
    return value
