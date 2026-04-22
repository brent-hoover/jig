"""Docker container launcher for the jig orchestrator.

On macOS (or any non-container host), ``jig start`` detects that it is
not inside Docker and re-execs itself inside the jig container image.
The project directory is volume-mounted at ``/project``.  The TUI stays
on the host and connects to the container's exposed WebSocket port.
"""

import logging
import os
import shutil
import subprocess
from pathlib import Path

_logger = logging.getLogger(__name__)

DEFAULT_IMAGE = "jig"
"""Default Docker image name.  Override with ``JIG_DOCKER_IMAGE`` env var."""

# Auth env var forwarded into the container.  Generate a long-lived
# OAuth token with ``claude setup-token`` and export it on the host as
# CLAUDE_CODE_OAUTH_TOKEN.
_AUTH_ENV_VAR = "CLAUDE_CODE_OAUTH_TOKEN"


def is_in_container() -> bool:
    """Return True if the current process is running inside the jig container."""
    return bool(os.environ.get("JIG_IN_CONTAINER")) or os.path.exists("/.dockerenv")


def docker_available() -> bool:
    """Return True if the Docker CLI is on PATH."""
    return shutil.which("docker") is not None


def image_exists(image: str | None = None) -> bool:
    """Return True if the jig Docker image has been built."""
    tag = image or os.environ.get("JIG_DOCKER_IMAGE", DEFAULT_IMAGE)
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", tag],
            capture_output=True,
        )
        return result.returncode == 0
    except FileNotFoundError:
        return False


def _find_jig_repo() -> Path | None:
    """Locate the jig source repo for building the Docker image.

    Walks from ``jig/__init__.py`` upward looking for a directory that
    contains both ``Dockerfile`` and ``pyproject.toml``.
    """
    start = Path(__file__).resolve().parent  # jig/ package dir
    for parent in [start, *start.parents]:
        if (parent / "Dockerfile").is_file() and (parent / "pyproject.toml").is_file():
            return parent
    return None


def build_image(image: str | None = None) -> None:
    """Build the jig Docker image from the source repo."""
    tag = image or os.environ.get("JIG_DOCKER_IMAGE", DEFAULT_IMAGE)
    repo = _find_jig_repo()
    if repo is None:
        raise FileNotFoundError(
            "Cannot find jig source directory (need Dockerfile + pyproject.toml). "
            f"Build manually: docker build -t {tag} /path/to/jig"
        )
    subprocess.run(
        ["docker", "build", "-t", tag, str(repo)],
        check=True,
    )


def exec_in_docker(
    project_path: Path,
    ws_port: int,
    verbose: bool,
) -> None:
    """Replace the current process with jig running inside Docker.

    Uses ``os.execvp`` so this function **does not return** on success.
    The caller's terminal is handed to the container.

    Mounts:
    - project directory → ``/project`` (rw)
    - ``~/.claude/`` → ``/home/jig/.claude`` (rw, settings/plugins)
    - ``~/.claude.json`` → ``/home/jig/.claude.json`` (rw)
    - ``~/.gitconfig`` → ``/home/jig/.gitconfig`` (ro)
    - ``~/.ssh/`` → ``/home/jig/.ssh`` (ro, for git-over-SSH)

    Auth: ``CLAUDE_CODE_OAUTH_TOKEN`` (from ``claude setup-token``)
    is forwarded from the host environment into the container.
    """
    image = os.environ.get("JIG_DOCKER_IMAGE", DEFAULT_IMAGE)

    args = [
        "docker",
        "run",
        "--rm",
        "-it",
        # bwrap needs mount-namespace capabilities and pivot_root (blocked
        # by Docker's default seccomp profile) inside the container
        "--cap-add",
        "SYS_ADMIN",
        "--security-opt",
        "seccomp=unconfined",
        # Project directory
        "-v",
        f"{project_path.resolve()}:/project",
        # WebSocket port for TUI
        "-p",
        f"{ws_port}:{ws_port}",
    ]

    # Claude settings and plugins
    claude_dir = Path.home() / ".claude"
    if claude_dir.is_dir():
        args.extend(["-v", f"{claude_dir}:/home/jig/.claude"])

    # Claude Code config
    claude_json = Path.home() / ".claude.json"
    if claude_json.is_file():
        args.extend(["-v", f"{claude_json}:/home/jig/.claude.json"])

    # Git config
    gitconfig = Path.home() / ".gitconfig"
    if gitconfig.is_file():
        args.extend(["-v", f"{gitconfig}:/home/jig/.gitconfig:ro"])

    # SSH keys for git-over-SSH remotes
    ssh_dir = Path.home() / ".ssh"
    if ssh_dir.is_dir():
        args.extend(["-v", f"{ssh_dir}:/home/jig/.ssh:ro"])

    # Forward auth token into the container
    auth_token = os.environ.get(_AUTH_ENV_VAR)
    if auth_token:
        args.extend(["-e", f"{_AUTH_ENV_VAR}={auth_token}"])
    else:
        _logger.warning(
            "CLAUDE_CODE_OAUTH_TOKEN not set. "
            "Run 'claude setup-token' and export the token."
        )

    # Image and command
    args.append(image)
    args.extend(["start", "--ws-port", str(ws_port)])
    if verbose:
        args.append("--verbose")

    _logger.info("launching container: %s", " ".join(args))
    os.execvp("docker", args)
