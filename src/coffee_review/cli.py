"""The ``coffee-review`` command line.

This module only parses arguments and calls pipeline functions; it holds no
logic of its own. Stage commands (``scrape``, ``parse``, ...) are added in the
milestone that builds each stage.
"""

from __future__ import annotations

from importlib.metadata import version
from typing import Annotated

import typer

app = typer.Typer(
    help="Scrape, parse, clean and enrich coffeereview.com reviews.",
    no_args_is_help=True,
)


def _print_version(value: bool) -> None:
    if value:
        typer.echo(f"coffee-review {version('coffee-review')}")
        raise typer.Exit


@app.callback()
def main(
    show_version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_print_version,
            is_eager=True,
            help="Show the version and exit.",
        ),
    ] = False,
) -> None:
    """Scrape, parse, clean and enrich coffeereview.com reviews."""
