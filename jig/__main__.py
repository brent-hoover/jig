"""Entry point for ``python -m jig``.

If invoked with no args (or `jig` shell wrapper with no args),
launches the TUI. With ``--print "<slash command>"``, runs the
command against the daemon and exits. Otherwise dispatches to the
click CLI.
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
        sys.argv = ["jig", "tui"]
    cli()


if __name__ == "__main__":
    main()
