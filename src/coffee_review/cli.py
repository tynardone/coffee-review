"""The ``coffee-review`` command line.

This module only parses arguments and calls pipeline functions; it holds no
logic of its own. Stage commands (``scrape``, ``parse``, ...) are added in the
milestone that builds each stage.
"""

from __future__ import annotations

from datetime import UTC, datetime
from importlib.metadata import version
from typing import Annotated

import typer
from pydantic import ValidationError
from rich.console import Console

from coffee_review.config import MAX_RATE_PER_SEC, get_settings
from coffee_review.logging_setup import configure_logging, scrape_log_path
from coffee_review.pipeline.scrape import scrape
from coffee_review.progress_display import RichScrapeProgress

# Exit codes. 2 is Click's own code for a usage error (a bad option), so a
# stopped run gets 3: a script can tell "you typed it wrong" from "the site
# pushed back".
EXIT_FINISHED = 0
EXIT_ERROR = 1
EXIT_STOPPED = 3
EXIT_CODES = {"finished": EXIT_FINISHED, "error": EXIT_ERROR, "stopped": EXIT_STOPPED}

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


@app.command("scrape")
def scrape_command(
    full: Annotated[
        bool,
        typer.Option("--full", help="Fetch every review, not just new or changed."),
    ] = False,
    limit: Annotated[
        int | None,
        typer.Option(min=1, help="Fetch at most this many pages this run."),
    ] = None,
    rate: Annotated[
        float | None,
        typer.Option(
            help=(
                f"Requests per second (default 5, at most {MAX_RATE_PER_SEC:g}). "
                "Concurrency is worked out from it."
            ),
        ),
    ] = None,
    concurrency: Annotated[
        int | None,
        typer.Option(
            help=(
                "Requests in flight at once; needs --rate. "
                "Between ceil(rate) and 2x that."
            ),
        ),
    ] = None,
    allow_delisting: Annotated[
        bool,
        typer.Option(
            "--allow-delisting",
            help=(
                "Mark reviews delisted even when so many left the sitemap that "
                "it looks broken. Use only when you know it's real."
            ),
        ),
    ] = False,
) -> None:
    """Download new and changed reviews into data/raw. Stops on any pushback.

    Exit codes: 0 finished, 1 error, 2 bad options, 3 stopped by the site.
    """
    settings = get_settings()
    if concurrency is not None and rate is None:
        raise typer.BadParameter(
            "--concurrency needs --rate; it only means something relative to a rate",
            param_hint="--concurrency",
        )
    if rate is not None:
        try:
            settings = settings.with_scrape_speed(rate, concurrency)
        except ValidationError as exc:
            raise typer.BadParameter(
                _first_message(exc), param_hint="--rate / --concurrency"
            ) from None

    now = datetime.now(UTC)
    # One console for log lines and the progress bar, so lines print above it.
    console = Console(stderr=True)
    configure_logging(
        settings.log_level, scrape_log_path(settings.data_dir, now), console=console
    )
    summary = scrape(
        settings,
        full=full,
        limit=limit,
        now=now,
        allow_delisting=allow_delisting,
        progress=RichScrapeProgress(console),
    )
    raise typer.Exit(EXIT_CODES[summary.outcome])


def _first_message(exc: ValidationError) -> str:
    message = str(exc.errors()[0]["msg"])
    return message.removeprefix("Value error, ")
