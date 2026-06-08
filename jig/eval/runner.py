"""Integration eval runner.

Chains the existing eval infrastructure into a zero-touch test:
  1. jig init (unattended via AutoPromptHandler + brief file)
  2. jig start subprocess on a pinned free port
  3. Race: project_complete event vs StallDetector vs wall-clock timeout
  4. On success: collect() + tracer check + copy analysis
  5. On failure: subprocess teardown + orphan cleanup
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import socket
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

log = logging.getLogger(__name__)

ALL_TOPICS = ("tickets", "spec", "agents", "events", "prompts")
_ANALYSIS_WAIT_SECONDS = 60.0
_ADDR_FILE_TIMEOUT = 15.0
_SIGKILL_GRACE = 10.0


def _free_port() -> int:
    """Bind to port 0, read the assigned port, and close."""
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


async def _wait_for_addr_file(path: Path, timeout: float = _ADDR_FILE_TIMEOUT) -> bool:
    """Poll path until it exists; return False on timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file():
            return True
        await asyncio.sleep(0.5)
    return False


class EvalOutcome(str, Enum):
    SUCCESS = "success"
    STALL = "stall"
    TIMEOUT = "timeout"
    INIT_ERROR = "init_error"
    TRACER_FAIL = "tracer_fail"


@dataclass
class RunResult:
    outcome: EvalOutcome
    stall_verdict: object = None
    manifest_path: Path | None = None
    analysis_dir: Path | None = None
    temp_dir: Path | None = None


async def _watch_completion(
    queue: asyncio.Queue[dict],
    *,
    analysis_wait: float = _ANALYSIS_WAIT_SECONDS,
) -> tuple[dict, str | None]:
    """Consume frames from queue until project_complete + analysis_complete arrive.

    Returns (project_complete_data, out_dir). out_dir is None if analysis_complete
    does not arrive within analysis_wait seconds of project_complete.
    """
    project_data: dict = {}
    project_complete_at: float | None = None

    while True:
        if project_complete_at is not None:
            remaining = analysis_wait - (time.monotonic() - project_complete_at)
            if remaining <= 0:
                return project_data, None
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=remaining)
            except asyncio.TimeoutError:
                return project_data, None
        else:
            msg = await queue.get()

        topic = msg.get("topic", "")
        kind = msg.get("kind", "")
        data = msg.get("data") or {}

        if topic == "events" and kind == "project_complete":
            project_data = data
            project_complete_at = time.monotonic()
        elif (
            topic == "events"
            and kind == "analysis_complete"
            and project_complete_at is not None
        ):
            return project_data, data.get("out_dir")


async def _watch_stall(
    queue: asyncio.Queue[dict],
    detector: object,
) -> object:
    """Feed frames into detector and poll until a stall verdict fires.

    Mirrors the consumer + poll pattern from jig.evals.watcher.run.
    """

    async def _consume() -> None:
        while True:
            msg = await queue.get()
            detector.observe(msg)  # type: ignore[attr-defined]

    async def _poll() -> object:
        interval = detector.thresholds.poll_interval_seconds  # type: ignore[attr-defined]
        while True:
            verdict = detector.check(now=time.monotonic())  # type: ignore[attr-defined]
            if verdict is not None:
                return verdict
            await asyncio.sleep(interval)

    consume_task = asyncio.create_task(_consume())
    poll_task = asyncio.create_task(_poll())
    done, pending = await asyncio.wait(
        {consume_task, poll_task}, return_when=asyncio.FIRST_COMPLETED
    )
    for task in pending:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    return poll_task.result()


def _check_tracer_outcome(exit_code: int, stdout: str) -> EvalOutcome | None:
    """Return TRACER_FAIL if the tracer indicates failure or SKIP; else None."""
    if exit_code != 0:
        return EvalOutcome.TRACER_FAIL
    if not stdout.strip():
        return EvalOutcome.TRACER_FAIL
    return None


def _teardown_proc(proc: subprocess.Popen, temp_path: Path) -> None:
    """SIGTERM proc, SIGKILL after grace period, clean up orphan subprocesses."""
    from jig.evals.watcher.run import _kill_orphan_subprocesses

    proc.terminate()
    try:
        proc.wait(timeout=_SIGKILL_GRACE)
    except subprocess.TimeoutExpired:
        proc.kill()
    _kill_orphan_subprocesses(temp_path, log.info)


async def run_eval(
    project_id: str,
    *,
    label: str | None,
    keep: bool,
    timeout_minutes: int,
    jig_repo: Path,
) -> RunResult:
    """Run a zero-touch integration eval for project_id.

    Returns a RunResult; the caller maps outcome to exit codes.
    """
    from websockets.asyncio.client import connect as _ws_connect

    import yaml

    from jig.eval.collector import collect
    from jig.evals.watcher.heuristics import StallThresholds
    from jig.evals.watcher.stall_detector import StallDetector
    from jig.init_prompts import AutoPromptHandler
    from jig.init_workflow import run_init

    brief_path = jig_repo / "evals" / "projects" / project_id / "brief.md"
    if not brief_path.is_file():
        log.error("brief not found: %s", brief_path)
        return RunResult(outcome=EvalOutcome.INIT_ERROR)

    timeout_seconds = timeout_minutes * 60
    temp_path = Path(tempfile.mkdtemp(prefix="jig-integration-"))
    port = _free_port()
    log.info("eval: project=%s temp=%s port=%d", project_id, temp_path, port)

    try:
        await run_init(
            name=str(temp_path),
            force=False,
            brief_file=brief_path,
            prompts=AutoPromptHandler(),
        )
    except Exception as exc:
        log.error("run_init failed: %s", exc)
        return RunResult(outcome=EvalOutcome.INIT_ERROR, temp_dir=temp_path)

    proc = subprocess.Popen(
        [
            "jig",
            "start",
            "--no-docker",
            "--path",
            str(temp_path),
            "--ws-port",
            str(port),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    addr_file = temp_path / ".jig" / "run" / "daemon.addr"
    if not await _wait_for_addr_file(addr_file):
        log.error("daemon addr file not found after %ss", _ADDR_FILE_TIMEOUT)
        _teardown_proc(proc, temp_path)
        return RunResult(outcome=EvalOutcome.INIT_ERROR, temp_dir=temp_path)

    detector = StallDetector(thresholds=StallThresholds())
    completion_q: asyncio.Queue[dict] = asyncio.Queue()
    stall_q: asyncio.Queue[dict] = asyncio.Queue()

    async def _dispatch(ws) -> None:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            await completion_q.put(msg)
            await stall_q.put(msg)

    outcome = EvalOutcome.TIMEOUT
    stall_verdict = None
    analysis_out_dir: str | None = None

    addr = f"ws://127.0.0.1:{port}"
    try:
        async with _ws_connect(addr, ping_interval=20) as ws:
            for topic in ALL_TOPICS:
                await ws.send(json.dumps({"type": "subscribe", "topics": [topic]}))

            dispatch_task = asyncio.create_task(_dispatch(ws))
            completion_task = asyncio.create_task(_watch_completion(completion_q))
            stall_task = asyncio.create_task(_watch_stall(stall_q, detector))
            timeout_task = asyncio.create_task(asyncio.sleep(timeout_seconds))

            done, pending = await asyncio.wait(
                {completion_task, stall_task, timeout_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            dispatch_task.cancel()
            for task in pending:
                task.cancel()
            for task in [dispatch_task, *pending]:
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

            if completion_task in done:
                _, analysis_out_dir = completion_task.result()
                outcome = EvalOutcome.SUCCESS
            elif stall_task in done:
                stall_verdict = stall_task.result()
                outcome = EvalOutcome.STALL
    except Exception as exc:
        log.error("WS error: %s", exc)
        outcome = EvalOutcome.INIT_ERROR

    if outcome != EvalOutcome.SUCCESS:
        log.info("eval: outcome=%s verdict=%s", outcome, stall_verdict)
        _teardown_proc(proc, temp_path)
        return RunResult(
            outcome=outcome,
            stall_verdict=stall_verdict,
            temp_dir=temp_path,
        )

    # Success path
    run_id = str(uuid.uuid4())[:8]
    tracer_sh = jig_repo / "evals" / "projects" / project_id / "tracer.sh"
    manifest = await collect(
        temp_path,
        run_id=run_id,
        project_id=project_id,
        label=label,
        tracer_cmd=["bash", str(tracer_sh)],
    )

    runs_root = jig_repo / "evals" / "runs"
    out_dir = runs_root / project_id / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.yaml"
    manifest_path.write_text(
        yaml.dump(manifest.model_dump(mode="json"), sort_keys=False, allow_unicode=True)
    )

    if manifest.tracer is not None:
        tracer_outcome = _check_tracer_outcome(
            manifest.tracer.exit_code, manifest.tracer.stdout
        )
        if tracer_outcome is not None:
            label_str = "SKIP" if manifest.tracer.exit_code == 0 else "FAIL"
            log.error(
                "tracer %s: exit_code=%d stdout=%r",
                label_str,
                manifest.tracer.exit_code,
                manifest.tracer.stdout[:100],
            )
            proc.terminate()
            shutil.rmtree(temp_path, ignore_errors=True)
            return RunResult(
                outcome=EvalOutcome.TRACER_FAIL, manifest_path=manifest_path
            )

    analysis_dir: Path | None = None
    if analysis_out_dir is not None:
        src = Path(analysis_out_dir)
        if src.is_dir():
            dst = out_dir / "analysis"
            shutil.copytree(src, dst)
            analysis_dir = dst
        else:
            log.warning("analysis_out_dir not a directory: %s", src)
    else:
        log.warning("analysis_complete not received; skipping analysis copy")

    proc.terminate()
    if not keep:
        shutil.rmtree(temp_path, ignore_errors=True)

    return RunResult(
        outcome=EvalOutcome.SUCCESS,
        manifest_path=manifest_path,
        analysis_dir=analysis_dir,
        temp_dir=temp_path if keep else None,
    )
