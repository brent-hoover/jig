#!/usr/bin/env python3
"""Reset a jig project to a fresh git repo, optionally installing a brief.

Usage (eval shorthand — recommended):
    scripts/project_reset.py --project hn-cli
    scripts/project_reset.py --project recipe-browser

    Resolves brief from   <jig_source>/evals/projects/<NAME>/brief.md
    Resets workspace at   <jig_source>/../jig_evals/<NAME>/
    Creates the workspace dir if it doesn't exist yet.

Usage (explicit paths):
    scripts/project_reset.py /absolute/path/to/project
    scripts/project_reset.py /abs/path/to/project --brief /abs/path/to/brief.md

What it does (in order):
1. Stops any running jig daemon for the project (best-effort), and
   kills any orphan listener on the daemon port.
2. Reads the brief content into memory (from ``--brief PATH`` /
   ``--project NAME`` if given, else from the project's own
   ``docs/brief.md`` if present).
3. Removes EVERYTHING in the project directory, including ``.git/`` —
   so old commit history and refs from prior eval runs cannot leak into
   the next run and confuse the agents.
4. Re-initializes git on branch ``develop`` and creates an empty
   ``chore: initialize repository`` commit so HEAD is valid.
5. Writes the preserved brief content (if any) to ``docs/brief.md``.

Refuses to run if the project path isn't absolute or isn't a directory
(unless ``--project`` is used, in which case the workspace is created).
``--brief`` (when given) must point to an existing readable file.
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


def _kill_orphan_on_port(port: int) -> None:
    """Best-effort: SIGTERM any process listening on the given TCP port.

    Catches the case where a prior ``jig`` launch left a daemon running
    but the PID file was wiped before ``jig daemon stop`` could find
    it. Without this, the next ``jig`` launch hits "address already in
    use" with no obvious recovery path. macOS-only (``lsof -ti``); on
    other platforms this silently no-ops.
    """
    import os
    import signal

    try:
        result = subprocess.run(
            ["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return  # no lsof available; nothing to do
    pids = [int(p) for p in result.stdout.split() if p.strip().isdigit()]
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
            print(f"[daemon] killed orphan listener pid={pid} on :{port}")
        except ProcessLookupError:
            pass
        except PermissionError:
            print(f"[daemon] cannot kill pid={pid} on :{port} (permission)")


def _resolve_eval_project(name: str) -> tuple[Path, Path]:
    """Resolve --project NAME → (workspace_path, brief_path).

    Convention: jig source lives at <repo>/, and eval workspaces live
    at <repo>/../jig_evals/<NAME>/. Brief files are checked into the
    jig repo at <repo>/evals/projects/<NAME>/brief.md.
    """
    here = Path(__file__).resolve().parent.parent  # scripts/.. → jig source
    workspace = here.parent / "jig_evals" / name
    brief = here / "evals" / "projects" / name / "brief.md"
    return workspace, brief


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "project_path",
        nargs="?",
        help=(
            "absolute path to the jig project directory. "
            "Required unless --project is given."
        ),
    )
    parser.add_argument(
        "--project",
        metavar="NAME",
        help=(
            "eval-project shorthand: resolves project_path to "
            "<repo>/../jig_evals/<NAME>/ and --brief to "
            "<repo>/evals/projects/<NAME>/brief.md. Mutually exclusive "
            "with --brief and a positional project_path."
        ),
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

    import shutil

    # --project resolves both project_path and --brief from convention.
    if args.project:
        if args.project_path or args.brief:
            print(
                "error: --project is mutually exclusive with project_path and --brief",
                file=sys.stderr,
            )
            return 2
        workspace, resolved_brief = _resolve_eval_project(args.project)
        if not resolved_brief.is_file():
            print(
                f"error: no brief for project {args.project!r} at {resolved_brief}",
                file=sys.stderr,
            )
            return 2
        # Create the workspace dir on first run for a project.
        if not workspace.exists():
            workspace.mkdir(parents=True)
            print(f"[workspace] created {workspace}")
        proj = workspace
        args.brief = str(resolved_brief)
    else:
        if not args.project_path:
            print(
                "error: project_path is required (or use --project NAME)",
                file=sys.stderr,
            )
            return 2
        proj = Path(args.project_path)

    if not proj.is_absolute():
        print(f"error: project_path must be absolute, got {proj}", file=sys.stderr)
        return 2
    if not proj.is_dir():
        print(f"error: not a directory: {proj}", file=sys.stderr)
        return 2

    # Resolve the source brief BEFORE we delete anything — the source
    # path may point inside the project (e.g. ``--brief docs/brief.md``)
    # and we're about to nuke everything.
    brief_content: bytes | None = None
    brief_origin = ""
    if args.brief:
        source_brief = Path(args.brief)
        if not source_brief.is_file():
            print(
                f"error: --brief path is not a file: {source_brief}",
                file=sys.stderr,
            )
            return 2
        brief_content = source_brief.read_bytes()
        brief_origin = f"--brief {source_brief}"
    else:
        in_place = proj / "docs" / "brief.md"
        if in_place.is_file():
            brief_content = in_place.read_bytes()
            brief_origin = f"existing {in_place.relative_to(proj)}"

    if brief_content is not None:
        print(f"[brief] preserved {len(brief_content)} bytes from {brief_origin}")
    else:
        print("[brief] no brief to preserve and no --brief given")

    # Stop daemon if tracked. Best-effort: a missing PID file shouldn't
    # block the reset. Read pid file BEFORE we wipe .jig/.
    if (proj / ".jig" / "run" / "daemon.pid").is_file():
        result = subprocess.run(
            ["uv", "run", "jig", "daemon", "stop", "--path", str(proj)],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            print("[daemon] stopped via PID file")
        else:
            print(f"[daemon] stop returned {result.returncode} (ignoring)")

    # Belt-and-suspenders: kill any orphan daemon listening on the
    # default WS port (19100). This catches the case where a previous
    # launch left a daemon running but its PID file was already wiped
    # by a partial reset, so ``jig daemon stop`` couldn't find it.
    _kill_orphan_on_port(19100)

    # Wipe EVERYTHING in the project directory, including .git/. Old
    # commit history (e.g. the previous scaffold commit, T1's branch
    # commits) would confuse agents on the next run.
    for child in proj.iterdir():
        if child.is_symlink() or child.is_file():
            child.unlink()
        else:
            shutil.rmtree(child)
    print("[wipe] removed all project contents (incl. .git/)")

    # Re-initialize git on develop, with an empty initial commit so HEAD
    # is valid before any further work. ``--no-verify`` and explicit
    # author/email envs would only matter if hooks were installed; an
    # empty repo has none, so skip them.
    _run(["git", "init", "-q", "-b", "develop"], cwd=proj)
    _run(
        [
            "git",
            "commit",
            "--allow-empty",
            "-q",
            "-m",
            "chore: initialize repository",
        ],
        cwd=proj,
    )
    print("[git] re-initialized on develop with empty root commit")

    # Restore the brief (if we had one).
    if brief_content is not None:
        brief_path = proj / "docs" / "brief.md"
        brief_path.parent.mkdir(parents=True, exist_ok=True)
        brief_path.write_bytes(brief_content)
        print(f"[brief] wrote docs/brief.md ({len(brief_content)} bytes)")

    print("done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
