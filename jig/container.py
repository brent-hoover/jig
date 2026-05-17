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

JIG_PROJECT_LABEL = "com.jig.project"
"""Docker label attached to each daemon container, set to the
project's resolved absolute path. Used to find orphans left over from
prior runs without false-matching unrelated jig containers."""

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
    - ``~/.claude/`` → ``/home/jig/.claude`` (ro, settings/plugins)
    - ``~/.claude.json`` → ``/home/jig/.claude.json`` (ro)
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
        f"127.0.0.1:{ws_port}:{ws_port}",
    ]

    # Claude settings and plugins
    claude_dir = Path.home() / ".claude"
    if claude_dir.is_dir():
        args.extend(["-v", f"{claude_dir}:/home/jig/.claude:ro"])

    # Claude Code config
    claude_json = Path.home() / ".claude.json"
    if claude_json.is_file():
        args.extend(["-v", f"{claude_json}:/home/jig/.claude.json:ro"])

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


def run_detached_container(
    project_path: Path,
    ws_port: int,
    *,
    verbose: bool = False,
    name: str | None = None,
) -> str:
    """Start the jig orchestrator in a detached Docker container.

    Returns the container ID (12-char short or full — whatever
    ``docker run -d`` prints to stdout). The container exposes the
    WebSocket port on the host. Use ``stop_detached_container`` to
    stop it.

    Mounts and auth follow the same pattern as ``exec_in_docker``.
    The command run inside the container is ``jig daemon serve``
    (NOT ``jig start`` — we want the orchestrator to run without
    re-execing into another Docker layer).
    """
    image = os.environ.get("JIG_DOCKER_IMAGE", DEFAULT_IMAGE)
    args = [
        "docker", "run", "-d", "--rm",
        "--cap-add", "SYS_ADMIN",
        "--security-opt", "seccomp=unconfined",
        "-v", f"{project_path.resolve()}:/project",
        "-p", f"127.0.0.1:{ws_port}:{ws_port}",
        "--label", f"{JIG_PROJECT_LABEL}={project_path.resolve()}",
    ]
    if name:
        args.extend(["--name", name])

    claude_dir = Path.home() / ".claude"
    if claude_dir.is_dir():
        args.extend(["-v", f"{claude_dir}:/home/jig/.claude:ro"])
    claude_json = Path.home() / ".claude.json"
    if claude_json.is_file():
        args.extend(["-v", f"{claude_json}:/home/jig/.claude.json:ro"])
    gitconfig = Path.home() / ".gitconfig"
    if gitconfig.is_file():
        args.extend(["-v", f"{gitconfig}:/home/jig/.gitconfig:ro"])
    ssh_dir = Path.home() / ".ssh"
    if ssh_dir.is_dir():
        args.extend(["-v", f"{ssh_dir}:/home/jig/.ssh:ro"])

    auth_token = os.environ.get(_AUTH_ENV_VAR)
    if auth_token:
        args.extend(["-e", f"{_AUTH_ENV_VAR}={auth_token}"])

    args.append(image)
    # Inside the container the orchestrator runs via the daemon-serve
    # subcommand. --path /project because we mounted the project there.
    args.extend(["daemon", "serve", "--path", "/project", "--ws-port", str(ws_port)])
    if verbose:
        args.append("--verbose")

    _logger.info("starting detached container: %s", " ".join(args))
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"docker run failed (rc={result.returncode}): {result.stderr.strip()}"
        )
    container_id = result.stdout.strip()
    if not container_id:
        raise RuntimeError(
            f"docker run returned no container id; stderr: {result.stderr.strip()}"
        )
    return container_id


def stop_detached_container(container_id: str, *, timeout: int = 5) -> bool:
    """Stop a detached jig daemon container. Returns True if it stopped
    (or wasn't running)."""
    result = subprocess.run(
        ["docker", "stop", "-t", str(timeout), container_id],
        capture_output=True, text=True, check=False,
    )
    return result.returncode == 0


def container_alive(container_id: str) -> bool:
    """Return True if the container is currently running."""
    result = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", container_id],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        return False
    return result.stdout.strip().lower() == "true"


def find_project_containers(project_path: Path) -> list[str]:
    """Return IDs of containers labeled with this project path.

    Includes stopped containers (``-a``) so we also catch zombie
    `--rm` containers whose deletion is hung. The label is set by
    ``run_detached_container``.
    """
    label_value = str(project_path.resolve())
    try:
        result = subprocess.run(
            ["docker", "ps", "-aq",
             "--filter", f"label={JIG_PROJECT_LABEL}={label_value}"],
            capture_output=True, text=True, check=False,
        )
    except FileNotFoundError:
        return []
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def force_remove_container(container_id: str) -> bool:
    """``docker rm -f`` a container. Returns True on success."""
    result = subprocess.run(
        ["docker", "rm", "-f", container_id],
        capture_output=True, text=True, check=False,
    )
    return result.returncode == 0
