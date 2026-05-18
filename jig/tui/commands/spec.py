"""``/spec`` slash command — read-only spec inspection (Track B Final).

Bones-Final subcommands:

- ``/spec capabilities --suite <id>`` — list every capability for one
  suite, drawn from the L3 brief's structured projection. Empty list
  when the brief hasn't been written yet.

This command stays read-only — capability authoring lives in the L3
PO + the SA. Operator inspection is what this provides.
"""

from __future__ import annotations

from typing import Any

from jig.tui.commands import register


@register("spec")
async def cmd_spec(
    *,
    args: list[str],
    orch,
    project_path,
    **_kwargs,
) -> dict[str, Any]:
    if not args:
        return {
            "ok": False,
            "error": "/spec needs a subcommand (capabilities)",
        }
    sub = args[0]
    rest = args[1:]
    if sub == "capabilities":
        suite_id = _parse_flag(rest, "--suite")
        if not suite_id:
            return {
                "ok": False,
                "error": "/spec capabilities requires --suite <id>",
            }
        return await _spec_capabilities(project_path, suite_id)
    return {"ok": False, "error": f"unknown /spec subcommand: {sub}"}


def _parse_flag(args: list[str], flag: str) -> str | None:
    """Pull the value following ``flag`` from ``args``, or None."""
    for i, a in enumerate(args):
        if a == flag and i + 1 < len(args):
            return args[i + 1]
    return None


async def _spec_capabilities(project_path, suite_id: str) -> dict[str, Any]:
    """Return the capability list from a suite's L3 structured spec.

    Fall-through behavior:
    - no suite index → ``{"capabilities": [], "status": "no-suite-index"}``
    - no brief on disk → ``{"capabilities": [], "status": "no-brief"}``
    - brief on disk → ``{"capabilities": [...], "status": "ok"}``
    """
    if project_path is None:
        return {"ok": False, "error": "/spec requires a project_path"}
    from pathlib import Path

    import yaml

    from jig.spec_loader import load_suites_index, suite_structured_path

    try:
        index = load_suites_index(project_path)
    except FileNotFoundError:
        return {"ok": True, "data": {"capabilities": [], "status": "no-suite-index"}}
    if index.suite_by_id(suite_id) is None:
        return {
            "ok": False,
            "error": f"suite {suite_id!r} not in suites.yaml",
        }

    # Reuse the spec_loader helper so suite_id goes through the
    # centralized safe-path validation; no raw join here.
    cache = suite_structured_path(Path(project_path), suite_id)
    if not cache.is_file():
        return {"ok": True, "data": {"capabilities": [], "status": "no-brief"}}
    raw = yaml.safe_load(cache.read_text()) or {}
    caps = []
    for c in raw.get("capabilities") or []:
        caps.append(
            {
                "id": c.get("id"),
                "title": c.get("title"),
                "state": c.get("state"),
                "summary": c.get("summary"),
            }
        )
    return {"ok": True, "data": {"capabilities": caps, "status": "ok"}}
