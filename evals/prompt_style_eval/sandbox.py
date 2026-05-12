"""Run a candidate's code against a task's hidden tests in a subprocess.

For v1, isolation is best-effort: the subprocess runs in a ``TemporaryDirectory``
with ``PYTHONPATH`` set to the tmpdir, with a hard wall-clock timeout and
SIGKILL on overrun. This protects against runaway hangs and accidentally
clobbering files in the harness cwd; it does not protect against an
adversarial process. The threat model is "stupid code", not "attacker."
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

from evals.prompt_style_eval.models import Task, TestResult


_SUMMARY_TOKEN_RE = re.compile(
    r"\b(\d+)\s+(passed|failed|error|errors|skipped|deselected|xfailed|xpassed)\b"
)


def parse_pytest_summary(stdout: str) -> tuple[int, int]:
    """Return ``(n_passed, n_failed)`` from pytest's ``-q`` summary lines.

    Scans the last ten lines for tokens like ``N passed``, ``N failed``,
    ``N error[s]``. Returns ``(0, 0)`` when no summary line is present
    (typical of collection errors).
    """
    n_passed = 0
    n_failed = 0
    lines = stdout.strip().splitlines()
    for line in lines[-10:]:
        for match in _SUMMARY_TOKEN_RE.finditer(line):
            count = int(match.group(1))
            label = match.group(2)
            if label == "passed":
                n_passed = count
            elif label in ("failed", "error", "errors"):
                n_failed += count
    return n_passed, n_failed


async def run_tests(code: str, task: Task, tests_dir: Path) -> TestResult:
    """Run ``task.test_command`` against ``code`` in a tmpdir sandbox.

    ``code`` is written to ``task.entrypoint`` inside a fresh tmpdir, the
    contents of ``tests_dir`` are copied to ``tmpdir/tests/``, and the
    subprocess runs with ``cwd=tmpdir`` and ``PYTHONPATH=tmpdir`` so the
    entrypoint module is importable from the tests. The subprocess is
    killed after ``task.timeout_s`` seconds.
    """
    with tempfile.TemporaryDirectory(prefix="pse-sandbox-") as tmp:
        tmp_path = Path(tmp)
        entrypoint = tmp_path / task.entrypoint
        entrypoint.parent.mkdir(parents=True, exist_ok=True)
        entrypoint.write_text(code, encoding="utf-8")

        shutil.copytree(tests_dir, tmp_path / "tests")

        env = os.environ.copy()
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            f"{tmp_path}{os.pathsep}{existing}" if existing else str(tmp_path)
        )

        start = time.perf_counter()
        timed_out = False
        proc = await asyncio.create_subprocess_exec(
            *task.test_command,
            cwd=tmp_path,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(),
                timeout=task.timeout_s,
            )
        except TimeoutError:
            timed_out = True
            proc.kill()
            stdout_b, stderr_b = await proc.communicate()
        duration = time.perf_counter() - start

        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")

        if timed_out:
            return TestResult(
                passed=False,
                n_passed=0,
                n_failed=0,
                duration_s=duration,
                stdout=stdout,
                stderr=stderr + f"\n[sandbox] killed after {task.timeout_s}s timeout",
            )

        n_passed, n_failed = parse_pytest_summary(stdout)
        return TestResult(
            passed=proc.returncode == 0,
            n_passed=n_passed,
            n_failed=n_failed,
            duration_s=duration,
            stdout=stdout,
            stderr=stderr,
        )


def default_pytest_command() -> list[str]:
    """Convenience: ``[sys.executable, '-m', 'pytest', '-q', 'tests/']``.

    Used as the default ``test_command`` in v1 task fixtures and tests. Real
    tasks may override.
    """
    return [sys.executable, "-m", "pytest", "-q", "tests/"]
