"""Live eval watcher.

Connects to a running jig daemon via WebSocket, subscribes to all
typed topics, and drives :class:`StallDetector`. When a stall verdict
fires:

1. Logs the signal + detail to ``evals/runs/<run-id>/watcher.log``.
2. Optionally kills orphan ``claude`` subprocesses rooted in the
   project workspace (the most common cause of heartbeat-gap stalls).
3. Invokes :mod:`evals.watcher.analyzer` to capture the run's state
   under a ``stalled`` outcome.
4. Exits with a non-zero status so a CI step can flag the run.

Usage:

    python -m jig.evals.watcher.run --project hn-cli
    python -m jig.evals.watcher.run /abs/path/to/workspace
    python -m jig.evals.watcher.run --project hn-cli --no-kill --no-analyze
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from websockets.asyncio.client import connect as _ws_connect

from jig.evals.watcher.heuristics import StallThresholds
from jig.evals.watcher.stall_detector import StallDetector, StallVerdict

ALL_TOPICS = ("tickets", "spec", "agents", "events", "prompts")


def _resolve_eval_project(name: str) -> Path:
    here = (
        Path(__file__).resolve().parents[3]
    )  # jig/evals/watcher/../../.. -> repo root
    return here.parent / "jig_evals" / name


def _resolve_addr(project_path: Path) -> str:
    addr_file = project_path / ".jig" / "run" / "daemon.addr"
    if addr_file.is_file():
        return addr_file.read_text().strip()
    return "ws://127.0.0.1:19100"


def _kill_orphan_subprocesses(project_path: Path, log) -> int:
    """SIGTERM any ``claude`` CLI subprocess whose CWD is inside the
    project's worktrees. Returns the number of PIDs signalled.

    macOS-only (uses ``lsof`` to find PIDs by working directory).
    Falls back to a no-op on platforms without lsof.
    """
    worktrees_root = project_path / ".jig" / "worktrees"
    if not worktrees_root.is_dir():
        return 0

    killed = 0
    try:
        # lsof -d cwd lists processes whose cwd is the given path.
        for child in worktrees_root.iterdir():
            if not child.is_dir():
                continue
            result = subprocess.run(
                ["lsof", "-d", "cwd", "-Fp", "+D", str(child)],
                capture_output=True,
                text=True,
                check=False,
            )
            for line in result.stdout.splitlines():
                if not line.startswith("p"):
                    continue
                try:
                    pid = int(line[1:])
                except ValueError:
                    continue
                # Only kill claude processes — be surgical.
                cmd = subprocess.run(
                    ["ps", "-p", str(pid), "-o", "command="],
                    capture_output=True,
                    text=True,
                    check=False,
                ).stdout.strip()
                if "claude" not in cmd:
                    continue
                try:
                    os.kill(pid, signal.SIGTERM)
                    log(f"[watcher] SIGTERM pid={pid} cwd={child.name}")
                    killed += 1
                except ProcessLookupError:
                    pass
    except FileNotFoundError:
        log("[watcher] lsof not available; skipping orphan cleanup")
    return killed


async def _consume(ws, detector: StallDetector, log) -> None:
    """Forward incoming WS frames into the detector. Runs until the
    connection drops or the watcher is cancelled."""
    async for frame in ws:
        try:
            msg = json.loads(frame)
        except json.JSONDecodeError:
            continue
        detector.observe(msg)


async def _poll(detector: StallDetector, log) -> StallVerdict:
    """Loop until ``detector.check()`` returns a verdict, then return it."""
    interval = detector.thresholds.poll_interval_seconds
    while True:
        verdict = detector.check()
        if verdict is not None:
            return verdict
        await asyncio.sleep(interval)


async def watch(
    *,
    project_path: Path,
    project_name: str,
    out_dir: Path,
    thresholds: StallThresholds,
    kill_orphans: bool,
    run_analyzer: bool,
) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "watcher.log"
    log_file = log_path.open("a", buffering=1)

    def log(msg: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        print(line)
        log_file.write(line + "\n")

    addr = _resolve_addr(project_path)
    log(f"[watcher] connecting to {addr}  (project={project_name})")
    log(
        f"[watcher] thresholds: bus={thresholds.bus_silence_seconds:.0f}s "
        f"hb={thresholds.heartbeat_gap_seconds:.0f}s "
        f"ni={thresholds.unanswered_needs_info_seconds:.0f}s "
        f"wt={thresholds.agent_wall_time_seconds:.0f}s "
        f"poll={thresholds.poll_interval_seconds:.0f}s"
    )

    detector = StallDetector(thresholds=thresholds)

    try:
        async with _ws_connect(addr, ping_interval=20) as ws:
            for topic in ALL_TOPICS:
                await ws.send(json.dumps({"type": "subscribe", "topics": [topic]}))
            log("[watcher] subscribed to all topics; watching for stalls")

            consumer = asyncio.create_task(_consume(ws, detector, log))
            poller = asyncio.create_task(_poll(detector, log))

            done, pending = await asyncio.wait(
                {consumer, poller},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            if poller in done:
                verdict: StallVerdict = poller.result()
            else:
                log("[watcher] WS consumer ended before any stall")
                return 0
    except Exception as exc:  # noqa: BLE001
        log(f"[watcher] connection error: {exc!r}")
        return 2

    log(f"[watcher] STALL DETECTED — signal={verdict.signal} {verdict.detail}")

    killed = 0
    if kill_orphans:
        killed = _kill_orphan_subprocesses(project_path, log)
        log(f"[watcher] killed {killed} orphan subprocess(es)")

    if run_analyzer:
        log("[watcher] running analyzer to capture stall report")
        from jig.evals.watcher.analyzer import analyze

        jig_repo = Path(__file__).resolve().parents[3]
        run_id = f"{project_name}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-stall"
        analysis_dir = jig_repo / "evals" / "runs" / run_id
        try:
            analyze(
                project_path=project_path,
                run_id=run_id,
                out_dir=analysis_dir,
                jig_repo=jig_repo,
                project_name=project_name,
                tags=["stall", verdict.signal],
                use_llm=True,
            )
            # Stamp the stall signal into metrics.json so the dashboard
            # picks it up.
            metrics_path = analysis_dir / "metrics.json"
            data = json.loads(metrics_path.read_text())
            data["stalls"] = {"detected": 1, "signal": verdict.signal}
            data["outcome"] = "stalled"
            data["outcome_reason"] = verdict.detail
            metrics_path.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n")
            log(f"[watcher] analyzer wrote {analysis_dir}")
        except Exception as exc:  # noqa: BLE001
            log(f"[watcher] analyzer failed: {exc!r}")

    log_file.close()
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "project_path",
        nargs="?",
        help="absolute path to the jig project workspace",
    )
    parser.add_argument(
        "--project",
        metavar="NAME",
        help="resolve project_path from <jig_repo>/../jig_evals/<NAME>/",
    )
    parser.add_argument(
        "--out-dir",
        help="dir for watcher.log + stall report "
        "(default: <jig_repo>/evals/runs/<NAME>-watcher/)",
    )
    parser.add_argument(
        "--bus-silence-seconds",
        type=float,
        default=StallThresholds.bus_silence_seconds,
    )
    parser.add_argument(
        "--heartbeat-gap-seconds",
        type=float,
        default=StallThresholds.heartbeat_gap_seconds,
    )
    parser.add_argument(
        "--unanswered-needs-info-seconds",
        type=float,
        default=StallThresholds.unanswered_needs_info_seconds,
    )
    parser.add_argument(
        "--agent-wall-time-seconds",
        type=float,
        default=StallThresholds.agent_wall_time_seconds,
    )
    parser.add_argument(
        "--poll-interval-seconds",
        type=float,
        default=StallThresholds.poll_interval_seconds,
    )
    parser.add_argument(
        "--no-kill",
        action="store_true",
        help="don't SIGTERM orphan claude subprocesses on stall",
    )
    parser.add_argument(
        "--no-analyze",
        action="store_true",
        help="don't invoke the analyzer to write a stall report",
    )
    args = parser.parse_args(argv)

    if args.project:
        if args.project_path:
            print(
                "error: --project is mutually exclusive with project_path",
                file=sys.stderr,
            )
            return 2
        proj = _resolve_eval_project(args.project)
        project_name = args.project
    else:
        if not args.project_path:
            print(
                "error: project_path is required (or use --project NAME)",
                file=sys.stderr,
            )
            return 2
        proj = Path(args.project_path)
        project_name = proj.name

    if not proj.is_absolute():
        print(f"error: project_path must be absolute: {proj}", file=sys.stderr)
        return 2
    if not proj.is_dir():
        print(f"error: not a directory: {proj}", file=sys.stderr)
        return 2

    jig_repo = Path(__file__).resolve().parents[3]
    out_dir = (
        Path(args.out_dir)
        if args.out_dir
        else jig_repo / "evals" / "runs" / f"{project_name}-watcher"
    )

    thresholds = StallThresholds(
        bus_silence_seconds=args.bus_silence_seconds,
        heartbeat_gap_seconds=args.heartbeat_gap_seconds,
        unanswered_needs_info_seconds=args.unanswered_needs_info_seconds,
        agent_wall_time_seconds=args.agent_wall_time_seconds,
        poll_interval_seconds=args.poll_interval_seconds,
    )

    return asyncio.run(
        watch(
            project_path=proj,
            project_name=project_name,
            out_dir=out_dir,
            thresholds=thresholds,
            kill_orphans=not args.no_kill,
            run_analyzer=not args.no_analyze,
        )
    )


if __name__ == "__main__":
    sys.exit(main())
