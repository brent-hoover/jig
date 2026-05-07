#!/usr/bin/env python3
"""Reset a jig project to its pre-init baseline, preserving docs/brief.md.

Usage:
    scripts/project_reset.py /absolute/path/to/project

What it does (in order):
1. Stops any running jig daemon for the project (best-effort).
2. Removes git worktrees and deletes every ``jig/*`` branch.
3. Hard-resets to the repo's root commit.
4. Runs ``git clean -fdx`` while excluding ``docs/brief.md`` (and its
   parent ``docs/`` dir if that's the only file in it) so the brief is
   never touched on disk.

Intended for the eval workflow: drop a fresh brief into a project, run
``jig init``, do an eval pass, then this script lets you start over from
the same brief without retyping it.

Refuses to run if the path isn't absolute, isn't a directory, or isn't a
git repository.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def _run(
    args: list[str],
    *,
    cwd: Path,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args, cwd=str(cwd), check=check, capture_output=True, text=True
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "project_path",
        help="absolute path to the jig project directory",
    )
    args = parser.parse_args(argv)

    proj = Path(args.project_path)
    if not proj.is_absolute():
        print(f"error: project_path must be absolute, got {proj}", file=sys.stderr)
        return 2
    if not proj.is_dir():
        print(f"error: not a directory: {proj}", file=sys.stderr)
        return 2
    if not (proj / ".git").exists():
        print(f"error: not a git repository: {proj}", file=sys.stderr)
        return 2

    brief_path = proj / "docs" / "brief.md"
    brief_present = brief_path.is_file()
    if brief_present:
        print(f"[preserve] docs/brief.md ({brief_path.stat().st_size} bytes)")
    else:
        print("[preserve] no docs/brief.md present")

    # Stop daemon if running. Best-effort: a missing daemon shouldn't
    # block the reset.
    if (proj / ".jig" / "run" / "daemon.pid").is_file():
        result = subprocess.run(
            ["uv", "run", "jig", "daemon", "stop", "--path", str(proj)],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            print("[daemon] stopped")
        else:
            print(f"[daemon] stop returned {result.returncode} (ignoring)")

    # Prune worktrees and delete jig/* branches BEFORE reset so refs
    # are still resolvable.
    try:
        _run(["git", "worktree", "prune"], cwd=proj)
        print("[git] pruned worktrees")
    except subprocess.CalledProcessError as exc:
        print(f"[git] worktree prune failed: {exc.stderr.strip()}")

    branches_out = _run(
        ["git", "branch", "--format=%(refname:short)"], cwd=proj
    ).stdout
    for raw in branches_out.splitlines():
        name = raw.strip()
        if not name.startswith("jig/"):
            continue
        result = subprocess.run(
            ["git", "branch", "-D", name],
            cwd=str(proj),
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            print(f"[git] deleted branch {name}")
        else:
            print(f"[git] could not delete {name}: {result.stderr.strip()}")

    # Hard-reset to the repo's root commit. This only affects TRACKED
    # files — the brief is untracked so it's not touched here.
    root_out = _run(
        ["git", "rev-list", "--max-parents=0", "HEAD"], cwd=proj
    ).stdout
    roots = [ln.strip() for ln in root_out.splitlines() if ln.strip()]
    if not roots:
        print("error: no root commit found; refusing to reset", file=sys.stderr)
        return 1
    root = roots[0]
    print(f"[git] resetting to root commit {root[:8]}")
    _run(["git", "reset", "--hard", root], cwd=proj)

    # Clean untracked files but exclude docs/brief.md so it stays on
    # disk untouched. ``git clean -d`` would otherwise remove the docs/
    # directory along with the brief; ``-e docs/brief.md`` excludes the
    # specific file, and excluding ``docs/`` keeps the directory.
    clean_args = ["git", "clean", "-fdx"]
    if brief_present:
        clean_args.extend(["-e", "docs/brief.md", "-e", "docs/"])
    _run(clean_args, cwd=proj)
    print(f"[git] cleaned untracked files (brief preserved: {brief_present})")

    # If brief.md was preserved, the docs/ dir still has it. If anything
    # else was in docs/ besides brief.md, it's still there too — remove
    # those leftovers manually so docs/ contains only the brief.
    if brief_present:
        docs_dir = proj / "docs"
        for child in docs_dir.iterdir():
            if child.resolve() == brief_path.resolve():
                continue
            if child.is_dir():
                import shutil
                shutil.rmtree(child)
            else:
                child.unlink()
        # Sanity check
        if not brief_path.is_file():
            print(
                "error: docs/brief.md was lost during clean — investigate",
                file=sys.stderr,
            )
            return 1

    print("done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
