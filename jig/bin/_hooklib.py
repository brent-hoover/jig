"""Shared helpers for the jig capability enforcement hooks.

The three ``check-*`` scripts at ``/jig/bin/`` (Phase 5 Task G, doc 16
§Hook compilation at spawn time) share three tasks:

1. Load ``/jig/policy/rules.json`` emitted by ``jig.capability_compiler``.
2. Decode the Claude Code hook payload from stdin (tool name + input).
3. Match paths and commands against declared globs and regexes.

This module keeps that logic in one place and well-tested. The scripts
that import it stay small enough to read at a glance — which matters
because the enforcement surface must be auditable.

Design rules:

* Pure stdlib (``json``, ``os``, ``re``, ``sys``, ``pathlib``) so the
  hooks run under the base Python 3 on the Docker image without any
  site-package dependency.
* Fail *closed* on malformed input or missing rules file — if the hook
  can't evaluate the call it denies by raising ``HookError``, which
  the script translates to exit code 2.
* No network, no subprocess, no logging side-effects — the hook's only
  outputs are its exit code and stderr.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, NamedTuple

# Sandbox-absolute path where the compiled ruleset is bind-mounted.
# Matches ``capability_compiler.SANDBOX_RULES_PATH`` — if that moves,
# move this too.
DEFAULT_RULES_PATH = "/jig/policy/rules.json"

# URI scheme → concrete sandbox path mapping. ``ticket://worktree/X``
# and ``repo://X`` both resolve to ``/workspace/X`` because the
# agent's git worktree IS the repo root inside the sandbox. Other
# schemes declared in the catalog (``project://``, ``role://``,
# ``decision://``, ``issue://``, non-``worktree`` ``ticket://`` bodies)
# refer to locations outside the sandbox and don't map to concrete
# paths here — Task F validation still accepts them so templates
# remain forward-compatible, but the hook treats them as "never
# matches a sandbox path" (see :func:`resolve_uri_glob`).


class HookError(Exception):
    """Raised when a hook cannot safely evaluate a tool call.

    The script wrappers catch this, print ``str(exc)`` to stderr, and
    exit 2 (deny). Fail-closed is the right default — a
    malformed payload or a missing rules file means something is wrong
    with the sandbox and the agent should not proceed as if the rule
    didn't exist."""


class HookPayload(NamedTuple):
    """Decoded Claude Code hook invocation.

    ``tool_name`` identifies the tool (e.g. ``"Bash"``, ``"Write"``);
    ``tool_input`` is the JSON object Claude Code passes through to
    the tool. We keep it as ``dict[str, Any]`` rather than typing per
    tool — the script that consumes it knows which field it cares
    about.

    ``NamedTuple`` rather than ``dataclass`` so the script can also be
    loaded by tests via ``importlib.util.spec_from_file_location`` —
    Python 3.14's dataclass machinery rejects frozen dataclasses in
    modules that aren't registered in ``sys.modules`` by the time the
    class body executes."""

    tool_name: str
    tool_input: dict[str, Any]


def read_payload(stdin: Any = None) -> HookPayload:
    """Parse Claude Code's hook JSON from stdin.

    Claude Code sends a JSON object containing at minimum
    ``tool_name`` and ``tool_input`` on every PreToolUse hook
    invocation. Unrecognised top-level keys are ignored — forward
    compatibility — but missing required keys raise ``HookError``."""

    source = stdin if stdin is not None else sys.stdin
    try:
        data = json.load(source)
    except json.JSONDecodeError as exc:
        raise HookError(f"hook stdin was not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise HookError(
            f"hook payload must be a JSON object, got {type(data).__name__}"
        )

    tool_name = data.get("tool_name")
    if not isinstance(tool_name, str) or not tool_name:
        raise HookError("hook payload missing non-empty 'tool_name'")

    tool_input = data.get("tool_input", {})
    if not isinstance(tool_input, dict):
        raise HookError(
            f"hook payload 'tool_input' must be an object, got {type(tool_input).__name__}"
        )

    return HookPayload(tool_name=tool_name, tool_input=tool_input)


def load_rules(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """Read and return the compiled ruleset as a dict.

    Defaults to :data:`DEFAULT_RULES_PATH`. Raises ``HookError`` if
    the file is missing or unparseable — the hook then denies rather
    than silently allowing (fail-closed)."""

    target = Path(path) if path is not None else Path(DEFAULT_RULES_PATH)
    try:
        raw = target.read_text()
    except OSError as exc:
        raise HookError(f"cannot read policy rules at {target}: {exc}") from exc
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HookError(f"policy rules at {target} are not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise HookError(
            f"policy rules at {target} must decode to a JSON object, "
            f"got {type(parsed).__name__}"
        )
    return parsed


def resolve_uri_glob(pattern: str) -> str | None:
    """Return the sandbox-absolute glob for ``pattern`` or ``None`` if
    the URI scheme doesn't map to a concrete sandbox path.

    URI-rooted patterns (``ticket://worktree/foo/**``, ``repo://foo/**``)
    get rewritten to their concrete prefix so the matcher can compare
    against absolute sandbox paths. Absolute patterns are returned
    unchanged. Patterns with an unmapped scheme return ``None`` — the
    caller treats that as a non-match rather than raising, because the
    catalog admits more schemes than the hook can currently resolve
    and forward-compatible templates should still enforce the rules
    the hook *can* evaluate."""

    if pattern.startswith("/"):
        return pattern
    if "://" not in pattern:
        # Catalog validation should have caught this — treat defensively
        # as non-matching rather than crashing on a malformed rules file.
        return None

    # Special-case ``ticket://worktree/...`` — the scheme key is
    # ``ticket``, but the body starts with ``worktree/``, and only that
    # worktree sub-path maps into the sandbox.
    if pattern.startswith("ticket://worktree/"):
        suffix = pattern[len("ticket://worktree") :]
        return "/workspace" + suffix
    if pattern == "ticket://worktree" or pattern == "ticket://worktree/":
        return "/workspace"

    # ``repo://X`` → ``/workspace/X`` (git worktree == repo root in sandbox)
    if pattern.startswith("repo://"):
        suffix = pattern[len("repo://") :]
        return "/workspace/" + suffix if suffix else "/workspace"

    return None


def match_glob(pattern: str, path: str) -> bool:
    """Return True if ``path`` matches the glob ``pattern``.

    Pattern semantics:

    * ``*`` — matches any characters within a single path segment
      (i.e. does not cross ``/``).
    * ``?`` — matches exactly one character within a segment.
    * ``**`` — matches zero or more *complete* path segments. So
      ``a/**/b`` matches ``a/b``, ``a/x/b``, ``a/x/y/b``; ``a/**``
      matches ``a``, ``a/x``, ``a/x/y``.

    Paths are compared segment-wise after splitting on ``/``. Trailing
    slashes are tolerated on both sides. ``fnmatch``-style character
    classes (``[abc]``) are not supported here — the declaration
    schema doesn't use them and reinventing that semantics would
    widen the audit surface without value."""

    pat_segs = _split_segments(pattern)
    path_segs = _split_segments(path)
    return _match_segs(pat_segs, 0, path_segs, 0)


def _split_segments(p: str) -> list[str]:
    # Normalise: strip leading slash (absolute paths) and trailing
    # slashes. Empty string after strip → empty list, not [""], so
    # ``match_glob("/", "/")`` handles correctly (both sides empty).
    stripped = p.strip("/")
    return stripped.split("/") if stripped else []


def _match_segs(pat: list[str], i: int, path: list[str], j: int) -> bool:
    while i < len(pat):
        if pat[i] == "**":
            # ``**`` at the end of the pattern absorbs all remaining
            # path segments (including zero).
            if i + 1 == len(pat):
                return True
            # Otherwise, try consuming 0..N path segments and see if
            # the rest of the pattern matches. Greedy doesn't matter
            # here — backtracking is bounded by path depth.
            for k in range(j, len(path) + 1):
                if _match_segs(pat, i + 1, path, k):
                    return True
            return False
        if j >= len(path):
            return False
        if not _match_single_segment(pat[i], path[j]):
            return False
        i += 1
        j += 1
    return j == len(path)


def _match_single_segment(pat: str, seg: str) -> bool:
    """Match a single path segment against a pattern that does not
    contain ``/`` or ``**`` (those are handled upstream). Supports
    ``*`` (any substring within segment) and ``?`` (one char)."""

    # Translate to a regex with the single-segment semantics.
    import re as _re

    regex = "".join(_translate_segment_char(c) for c in pat)
    return _re.fullmatch(regex, seg) is not None


def _translate_segment_char(c: str) -> str:
    import re as _re

    if c == "*":
        return ".*"
    if c == "?":
        return "."
    return _re.escape(c)


def normalize_path(raw: str, *, cwd: str = "/workspace") -> str:
    """Return ``raw`` as a collapsed absolute path.

    Relative paths are resolved against ``cwd`` (the sandbox working
    directory, which is always ``/workspace`` in production). We use
    ``os.path.normpath`` rather than ``Path.resolve()`` because we
    don't want to follow symlinks during policy evaluation — a symlink
    from ``/workspace/escape`` to ``/`` should not let an agent read
    anywhere.

    Empty input raises ``HookError`` — every tool call this hook gates
    has a path input, so an empty one is malformed."""

    if not raw:
        raise HookError("tool input path was empty")
    if not os.path.isabs(raw):
        raw = os.path.join(cwd, raw)
    # normpath collapses ``..`` lexically; combined with the bwrap
    # filesystem scope this is sufficient for the string match.
    return os.path.normpath(raw)


def denies_path(path: str, patterns: list[str]) -> str | None:
    """Return the first ``patterns`` entry that matches ``path`` or
    ``None``. Patterns are resolved through
    :func:`resolve_uri_glob`; unresolvable ones are skipped (they
    cannot apply to a sandbox-absolute path)."""

    for pat in patterns:
        resolved = resolve_uri_glob(pat)
        if resolved is None:
            continue
        if match_glob(resolved, path):
            return pat
    return None


def writable_permits_path(path: str, writable: list[str]) -> bool:
    """Return True if ``path`` matches any of the ``writable`` patterns.

    A spawn declaring an empty ``writable`` list falls back to
    permissive — the hook is only registered when either ``writable``
    or ``denied`` is non-empty, so this helper is only called when at
    least one of them matters."""

    for pat in writable:
        resolved = resolve_uri_glob(pat)
        if resolved is None:
            continue
        if match_glob(resolved, path):
            return True
    return False


def deny_exit(reason: str) -> None:
    """Print ``reason`` to stderr and exit 2 (Claude Code's "deny" code)."""
    print(reason, file=sys.stderr)
    sys.exit(2)


def allow_exit() -> None:
    """Exit 0 (Claude Code's "allow" code)."""
    sys.exit(0)
