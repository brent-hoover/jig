"""Allow ``python -m myproject`` alongside the ``myproject`` entry point."""

from myproject.cli import app

if __name__ == "__main__":  # pragma: no cover
    app()
