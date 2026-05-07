#!/usr/bin/env python3
"""Reset a jig project to its pre-init baseline, optionally installing a brief.

Usage:
    scripts/project_reset.py /absolute/path/to/project
    scripts/project_reset.py /abs/path/to/project --brief /abs/path/to/brief.md

What it does (in order):
1. Stops any running jig daemon for the project (best-effort).
2. Removes git worktrees and deletes every ``jig/*`` branch.
3. Hard-resets to the repo's root commit.
4. Runs ``git clean -fdx`` while excluding ``docs/brief.md`` (and its
   parent ``docs/`` dir) so the brief is never touched on disk.
5. If ``--brief PATH`` is given, copies that file to ``docs/brief.md``
   (replacing any existing brief). Without ``--brief``, the existing
   ``docs/brief.md`` (if any) is preserved in place.

Intended for the eval workflow: keep a library of brief files
(``evals/briefs/hn-cli.md`` etc.) and call this script to install one
into a target project after a clean reset.

Refuses to run if the project path isn't absolute, isn't a directory,
or isn't a git repository. ``--brief`` (when given) must point to an
existing readable file.
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
    parser.add_argument(
        "--brief",
        metavar="PATH",
        help=(
            "path to a brief file to install at docs/brief.md after reset. "
            "Replaces any existing brief. If omitted, an existing "
            "docs/brief.md is preserved in place."
        ),
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

    source_brief: Path | None = None
    if args.brief:
        source_brief = Path(args.brief)
        if not source_brief.is_file():
            print(
                f"error: --brief path is not a file: {source_brief}",
                file=sys.stderr,
            )
            return 2

    brief_path = proj / "docs" / "brief.md"
    brief_present = brief_path.is_file()
    if source_brief is not None:
        print(f"[brief] will install from {source_brief}")
    elif brief_present:
        print(f"[preserve] docs/brief.md ({brief_path.stat().st_size} bytes)")
    else:
        print("[preserve] no docs/brief.md present and no --brief given")

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

    # Clean untracked files. When --brief is given we'll write the brief
    # ourselves afterwards so we can let git clean wipe everything; when
    # preserving in place, exclude docs/brief.md (and docs/) so the file
    # stays on disk untouched.
    clean_args = ["git", "clean", "-fdx"]
    if source_brief is None and brief_present:
        clean_args.extend(["-e", "docs/brief.md", "-e", "docs/"])
    _run(clean_args, cwd=proj)
    preserved_in_place = source_brief is None and brief_present
    print(
        f"[git] cleaned untracked files "
        f"(brief preserved in place: {preserved_in_place})"
    )

    # If we preserved in place and there are leftovers in docs/ besides
    # the brief itself, remove them so docs/ contains only the brief.
    if preserved_in_place:
        docs_dir = proj / "docs"
        for child in docs_dir.iterdir():
            if child.resolve() == brief_path.resolve():
                continue
            if child.is_dir():
                import shutil

                shutil.rmtree(child)
            else:
                child.unlink()
        if not brief_path.is_file():
            print(
                "error: docs/brief.md was lost during clean — investigate",
                file=sys.stderr,
            )
            return 1

    # Install the source brief if --brief was given.
    if source_brief is not None:
        brief_path.parent.mkdir(parents=True, exist_ok=True)
        import shutil

        shutil.copyfile(source_brief, brief_path)
        size = brief_path.stat().st_size
        print(f"[brief] installed docs/brief.md from {source_brief} ({size} bytes)")

    print("done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
