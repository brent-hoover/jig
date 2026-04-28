"""Entry point for ``python -m jig``.

If invoked with no args (or `jig` shell wrapper with no args),
auto-starts the daemon for cwd if not running, then launches the TUI.
With ``--print "<slash command>"``, runs the command against the daemon
and exits. Otherwise dispatches to the click CLI.
"""
import sys

from jig.cli import cli


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] == "--print":
        if len(args) < 2:
            print("--print requires a slash command argument", file=sys.stderr)
            sys.exit(2)
        from jig.tui.print_mode import run_print

        sys.exit(run_print(args[1]))
    if not args:
        # No-args mode: auto-start the daemon for cwd if not running, then
        # launch the TUI. The daemon now tolerates "no project" (orchestrator
        # starts in unconfigured mode, /init bootstraps).
        from pathlib import Path

        from jig.daemon import daemon_start, daemon_status

        cwd = Path.cwd()
        try:
            status = daemon_status(cwd)
            if not status.running:
                daemon_start(cwd)
        except Exception as exc:
            print(f"warning: could not auto-start daemon: {exc}", file=sys.stderr)
            # Continue — the TUI will show "daemon: reconnecting" if the
            # operator wants to debug separately.
        sys.argv = ["jig", "tui"]
    cli()


if __name__ == "__main__":
    main()
