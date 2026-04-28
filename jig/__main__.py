"""Entry point for ``python -m jig``.

If invoked with no args (or `jig` shell wrapper with no args),
launches the TUI. Otherwise dispatches to the click CLI.
"""
import sys

from jig.cli import cli


def main() -> None:
    if len(sys.argv) == 1:
        sys.argv = ["jig", "tui"]
    cli()


if __name__ == "__main__":
    main()
