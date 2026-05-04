"""Centralized safe-path validation for id-derived filesystem paths.

Every place in the codebase that lets an external string (ticket id,
module id, screen id, service id, journey id, agent-supplied path…)
land in a filesystem path must run that string through this module
first. The validator is deliberately strict so that malicious or
malformed identifiers cannot:

- escape the intended root via ``..`` traversal
- alias a hidden file via a leading ``.``
- alias a CLI flag via a leading ``-``
- collide with absolute paths via a leading ``/`` or backslash
- abuse case-insensitive filesystems via uppercase variants
- produce dangerous git refs via shell metacharacters

The module exposes three layers, in increasing permissiveness:

- ``is_safe_path_segment`` / ``validate_safe_path_segment`` — strict
  kebab-or-snake-case identifiers. Required for every id-shaped
  segment that becomes a directory or branch name.
- ``is_safe_filename`` — same rules, but allows interior ``.`` so a
  filename like ``foo.py`` or ``screen-001.html`` is accepted.
  Leading dot is still rejected.
- ``safe_join`` / ``safe_resolve_within`` — combine the above with a
  final ``resolve().is_relative_to(root.resolve())`` containment
  check, so even a symlink escape is caught.
"""

from __future__ import annotations

import re
from pathlib import Path

# A safe path segment must:
# - start with [a-z0-9] (no leading dash, no leading dot, no leading underscore)
# - contain only [a-z0-9_-] thereafter
# - be at most 100 characters long (cap enforced separately so the regex
#   message in tests is more obvious; the regex would otherwise just say
#   "no match")
_SAFE_SEGMENT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_SAFE_FILENAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.\-]*$")
_MAX_SEGMENT_LEN = 100


def is_safe_path_segment(segment: object) -> bool:
    """Return True iff ``segment`` is a safe single path segment.

    Used for id-shaped segments only. Filenames with extensions need
    ``is_safe_filename`` instead.
    """
    if not isinstance(segment, str):
        return False
    if not segment or len(segment) > _MAX_SEGMENT_LEN:
        return False
    return _SAFE_SEGMENT_RE.match(segment) is not None


def is_safe_filename(name: object) -> bool:
    """Return True iff ``name`` is a safe single filename.

    Same rules as ``is_safe_path_segment`` but allows interior dots
    (for extensions like ``.py`` or ``.contracts.yaml``). Leading
    dots, slashes, and uppercase characters are still rejected so
    hidden files and traversal cannot sneak in.
    """
    if not isinstance(name, str):
        return False
    if not name or len(name) > _MAX_SEGMENT_LEN:
        return False
    if name in {".", ".."}:
        return False
    return _SAFE_FILENAME_RE.match(name) is not None


def validate_safe_path_segment(segment: str, name: str) -> str:
    """Raise ``ValueError`` if ``segment`` is unsafe; return it on success.

    ``name`` identifies the field in the error message
    (e.g. ``"ticket.id"`` or ``"module_id"``) so the operator can
    trace which input was rejected.
    """
    if not is_safe_path_segment(segment):
        raise ValueError(
            f"{name}={segment!r} is not a safe path segment "
            f"(must match ^[a-z0-9][a-z0-9_-]*$, length <= {_MAX_SEGMENT_LEN})"
        )
    return segment


def validate_safe_filename(name: str, field: str) -> str:
    """Raise ``ValueError`` if ``name`` is not a safe filename; return it on success."""
    if not is_safe_filename(name):
        raise ValueError(
            f"{field}={name!r} is not a safe filename "
            f"(must match ^[a-z0-9][a-z0-9_.\\-]*$, length <= {_MAX_SEGMENT_LEN})"
        )
    return name


def safe_join(root: Path, *segments: str) -> Path:
    """Join ``segments`` under ``root`` with strict per-segment + containment checks.

    Each segment must pass ``validate_safe_path_segment``. After the
    join, the resolved final path must be relative to the resolved
    ``root`` — this catches symlink escapes that the per-segment
    check cannot.
    """
    if not segments:
        raise ValueError("safe_join requires at least one segment")
    for seg in segments:
        validate_safe_path_segment(seg, "path segment")
    root_resolved = root.resolve()
    candidate = root.joinpath(*segments)
    # ``strict=False`` so we can validate paths that don't exist yet
    # (e.g. about to be created). The resolution still walks symlinks
    # for any ancestors that *do* exist.
    candidate_resolved = candidate.resolve()
    if not _is_relative_to(candidate_resolved, root_resolved):
        raise ValueError(
            f"path {candidate} resolves outside root {root_resolved}"
        )
    return candidate


def safe_resolve_within(root: Path, relative: str) -> Path:
    """Resolve an agent-supplied ``relative`` path under ``root`` with containment.

    Use this for agent-facing path arguments like ``"src/foo/bar.py"``
    or ``"README.md"`` — paths the agent passes via MCP that point
    into existing source. The rules are deliberately looser than
    ``safe_join`` because real source trees contain uppercase
    filenames (``README.md``, ``Cargo.toml``, ``Dockerfile``) and we
    don't want to gratuitously block legitimate reads.

    Rejected:
    - empty input
    - absolute paths (leading ``/`` or ``\\``)
    - any segment that is ``.`` or ``..`` (traversal)
    - any segment containing ``\\`` (Windows-style traversal)
    - any segment with NUL bytes
    - empty intermediate segments (``foo//bar``)
    - leading-dot segments other than the conventional ``..`` reject
      above (so ``.env`` and ``.git`` cannot be opened by an agent;
      hidden state is operator-managed, not agent-touchable)

    The final containment check (``resolve().relative_to(root)``)
    catches symlink escapes that survive the per-segment rules.
    """
    if not isinstance(relative, str) or not relative:
        raise ValueError("relative path must be a non-empty string")
    if relative.startswith("/") or relative.startswith("\\"):
        raise ValueError(f"relative path {relative!r} must not be absolute")
    if "\x00" in relative:
        raise ValueError("relative path contains NUL byte")
    parts = relative.split("/")
    for part in parts:
        if not part:
            raise ValueError(
                f"relative path {relative!r} contains empty segments"
            )
        if part in {".", ".."}:
            raise ValueError(
                f"relative path {relative!r} contains traversal segment"
            )
        if "\\" in part:
            raise ValueError(
                f"relative path {relative!r} contains backslash"
            )
        if part.startswith("."):
            raise ValueError(
                f"relative path {relative!r} has hidden-file segment {part!r}"
            )

    root_resolved = root.resolve()
    candidate = root.joinpath(*parts)
    candidate_resolved = candidate.resolve()
    if not _is_relative_to(candidate_resolved, root_resolved):
        raise ValueError(
            f"path {candidate} resolves outside root {root_resolved}"
        )
    return candidate_resolved


def _is_relative_to(path: Path, root: Path) -> bool:
    """Backport of ``Path.is_relative_to`` semantics on Python ≥ 3.9.

    Built-in is fine on 3.12, but kept as a private helper so a
    potential future relaxation (e.g. allow equality but not strict
    descendant) is centralized.
    """
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
