"""Error types for project URI parsing and resolution."""
from __future__ import annotations


class ProjectUriError(ValueError):
    """Raised when a project:// URI is malformed or cannot be resolved."""


class UnknownAuthorityError(ProjectUriError):
    """Raised when an authority segment is not one of the known kinds.

    This is treated as a code bug (caller built the URI wrong), not as user input
    requiring a friendly message — the authority list is closed.
    """


class UnimplementedAuthorityError(ProjectUriError):
    """Raised when an authority parses but its resolver is not yet wired up.

    For v2 bones the spec resolver is live and others raise this at resolve time.
    Callers can distinguish "URI is malformed" (ProjectUriError) from "URI is
    well-formed but the resolver isn't built yet" (UnimplementedAuthorityError).
    """
