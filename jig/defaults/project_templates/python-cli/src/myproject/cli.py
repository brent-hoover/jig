"""CLI entry point. Add commands here as the project grows."""

from __future__ import annotations

import typer

app = typer.Typer(help="myproject CLI.")


@app.command()
def hello(name: str = "world") -> None:
    """Placeholder command — replace with your first real command."""
    typer.echo(f"Hello, {name}!")


if __name__ == "__main__":  # pragma: no cover
    app()
