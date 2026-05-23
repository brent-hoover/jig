"""Path-scoping helpers for reviewer file-scoping (RoleConfig.reads_glob).

Two pure helpers shared by the MCP tools (``reviewer_get_diff``,
``reviewer_read_file``) and the routing-layer defence-in-depth in
``jig.reviewer_routing``.

* :func:`path_in_scope` — boolean predicate. Does ``path`` match any
  ``include`` glob AND not any ``exclude`` glob?
* :func:`glob_to_git_pathspec` — converts an include + exclude list
  into the argument list passed after ``--`` to ``git diff``, using
  git's ``:(exclude)`` pathspec magic for excludes.

Glob semantics — what operators expect from ``src/**``:

* ``**`` (between separators, leading, or trailing) matches any
  number of path components, including zero. ``src/**`` matches
  ``src/foo.py``, ``src/deep/nested.py``, and ``src`` itself.
* ``*`` matches any characters within a single component (no ``/``).
* ``?`` matches a single character (no ``/``).
* Other characters are literal.

``pathlib.PurePosixPath.match`` was rejected because its ``**``
semantics are surprisingly restrictive (``src/**`` matches only the
immediate children, not deeper paths). ``PurePath.full_match`` has
the right semantics but landed in Python 3.13 and the project
floor is 3.12. A small fnmatch-derived regex translator gives us
the right semantics on every supported Python.

The functions are deliberately pure: no I/O, no logging, no
exceptions on bad input (caller validates upstream). Returns are
deterministic for unit-test stability.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import PurePosixPath


def path_in_scope(
    path: str,
    *,
    include: list[str],
    exclude: list[str],
) -> bool:
    """Return True if ``path`` matches the include-set and not the exclude-set.

    Semantics:

    * Empty ``include`` means "no scoping" — every non-empty,
      non-traversal path is in-scope (matches historical
      unscoped-role behaviour). Callers that want a deny-by-default
      policy must pass a non-empty include list.
    * Non-empty ``include`` requires AT LEAST ONE pattern to match.
    * Any ``exclude`` match wins — even when an include pattern also
      matches. Exclusion is the final say.
    * Empty paths, absolute paths (starting with ``/``), and paths
      containing ``..`` are rejected regardless of patterns.

    Args:
        path: Project-relative POSIX path (e.g. ``"src/foo.py"``).
        include: Include globs. Empty list = no scoping (all in).
        exclude: Exclude globs. Empty list = no exclusions.

    Returns:
        True when the path is reachable by the scope, False otherwise.
    """
    if not path:
        return False
    if path.startswith("/"):
        return False
    parts = PurePosixPath(path).parts
    if not parts:
        # Empty parts means the path normalised to nothing (``.`` or
        # similar) — no concrete file to scope.
        return False
    if ".." in parts or "." in parts:
        return False
    if include:
        if not any(_match(path, pattern) for pattern in include):
            return False
    if any(_match(path, pattern) for pattern in exclude):
        return False
    return True


def glob_to_git_pathspec(
    include: list[str],
    exclude: list[str],
) -> list[str]:
    """Translate an include + exclude glob list into a git pathspec list.

    The output is the argument list that follows ``--`` in a
    ``git diff`` invocation. Both include and exclude entries use
    git's ``:(glob)`` pathspec magic so ``*`` and ``**`` behave the
    same way our ``path_in_scope`` predicate does — ``*`` confined
    to a single component, ``**`` spanning components. Without
    ``:(glob)`` git falls back to fnmatch semantics where ``*``
    can match ``/`` boundaries, which diverges from the
    in-process predicate and lets ``reviewer_get_diff`` expose
    files that ``reviewer_read_file`` would refuse.

    Args:
        include: Include globs. Empty → returns empty list, meaning
            git diff sees no path restriction.
        exclude: Exclude globs.

    Returns:
        Argument list for ``git diff -- <pathspec>...``.
    """
    pathspec: list[str] = [f":(glob){pattern}" for pattern in include]
    pathspec.extend(f":(exclude,glob){pattern}" for pattern in exclude)
    return pathspec


# ---------------------------------------------------------------------------
# Glob → regex translation
# ---------------------------------------------------------------------------


def _match(path: str, pattern: str) -> bool:
    """Return True if ``path`` matches ``pattern`` under our glob rules."""
    return _compile(pattern).fullmatch(path) is not None


@lru_cache(maxsize=512)
def _compile(pattern: str) -> re.Pattern[str]:
    """Compile a glob pattern to a regex.

    ``**`` matches across separators (zero or more components),
    ``*`` and ``?`` are confined to a single component. Cached
    because the same handful of patterns repeats per scoped role.
    """
    return re.compile(_translate(pattern))


def _translate(pattern: str) -> str:
    """Translate a glob pattern to a regex source string.

    Walks the pattern character-by-character, emitting regex
    fragments. Handles ``**`` specially:

    * ``**/`` → ``(?:.*/)?`` (any prefix path including empty)
    * Leading ``**/`` is normal — collapses to the same form.
    * Trailing ``/**`` → ``(?:/.*)?`` (any suffix including empty,
      so ``src/**`` matches ``src``, ``src/foo``, ``src/a/b``)
    * Bare ``**`` (alone) → ``.*``
    * ``**`` mid-pattern between non-separators is illegal in
      gitignore-style globs but we treat as ``.*`` for robustness.

    Single ``*`` matches ``[^/]*``; ``?`` matches ``[^/]``.
    """
    i = 0
    parts: list[str] = []
    n = len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            # Look for ``**``.
            if i + 1 < n and pattern[i + 1] == "*":
                # ``**`` — figure out the surrounding separators.
                # Cases:
                #   ``**/<rest>``  → ``(?:.*/)?<rest>``
                #   ``<pre>/**/<rest>`` → ``<pre>(?:/.*)?/<rest>``
                #     (handled by leading-slash awareness below)
                #   ``<pre>/**`` (trailing) → ``<pre>(?:/.*)?``
                #   ``**`` (alone) → ``.*``
                after = i + 2
                if after < n and pattern[after] == "/":
                    # ``**/...`` — consume the slash too; any path
                    # prefix matches including empty.
                    parts.append(r"(?:.*/)?")
                    i = after + 1
                    continue
                if after == n:
                    # Trailing ``**``. If we have a preceding slash
                    # we wrote it literally already; need to make it
                    # optional. Look back at the last emitted part.
                    if parts and parts[-1] == "/":
                        parts.pop()
                        parts.append(r"(?:/.*)?")
                    else:
                        parts.append(r".*")
                    i = after
                    continue
                # ``**`` followed by something other than ``/`` —
                # treat as ``.*`` (lenient).
                parts.append(r".*")
                i = after
                continue
            # Single ``*`` — within-component wildcard.
            parts.append(r"[^/]*")
            i += 1
            continue
        if c == "?":
            parts.append(r"[^/]")
            i += 1
            continue
        if c == ".":
            parts.append(r"\.")
            i += 1
            continue
        if c in "+()|^$":
            parts.append("\\" + c)
            i += 1
            continue
        if c == "[":
            # Character class — copy verbatim until the closing ``]``.
            end = pattern.find("]", i + 1)
            if end == -1:
                # Unclosed — treat literally.
                parts.append(re.escape(c))
                i += 1
                continue
            parts.append(pattern[i : end + 1])
            i = end + 1
            continue
        # Default: any other character is a literal. Escape regex
        # metacharacters that we haven't handled explicitly — ``{``,
        # ``}``, ``\``, etc. — so they match themselves in paths
        # (legal but unusual filenames) rather than causing
        # ``re.error`` or wrong matches.
        parts.append(re.escape(c))
        i += 1
    return "".join(parts)
