"""The ``coffee-review`` command line.

This module only parses arguments and calls pipeline functions; it holds no
logic of its own. Stage commands (``scrape``, ``parse``, ...) are added in the
milestone that builds each stage.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from importlib.metadata import version
from typing import TYPE_CHECKING, Annotated, Literal

import typer
from pydantic import ValidationError
from rich.console import Console

from coffee_review.config import MAX_RATE_PER_SEC, get_settings
from coffee_review.logging_setup import FILE_ONLY, configure_logging, run_log_path
from coffee_review.pipeline.clean import clean
from coffee_review.pipeline.economic import cpi, fx
from coffee_review.pipeline.parse import parse
from coffee_review.pipeline.roaster_review import (
    InvalidReviewSheetError,
    ReviewSheetMissingError,
    apply_roaster_review,
)
from coffee_review.pipeline.scrape import scrape
from coffee_review.progress_display import RichRunProgress
from coffee_review.storage.clean_store import UnreadableReviewSheetError

if TYPE_CHECKING:
    from collections.abc import Callable

    from coffee_review.config import Settings
    from coffee_review.pipeline.economic import EconomicRunResult

# Exit codes. 2 is Click's own code for a usage error (a bad option), so a
# stopped run gets 3: a script can tell "you typed it wrong" from "the site
# pushed back".
EXIT_FINISHED = 0
EXIT_ERROR = 1
EXIT_STOPPED = 3
EXIT_CODES = {"finished": EXIT_FINISHED, "error": EXIT_ERROR, "stopped": EXIT_STOPPED}

logger = logging.getLogger(__name__)

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
        settings.log_level,
        run_log_path(settings.data_dir, "scrape", now),
        console=console,
    )
    summary = scrape(
        settings,
        full=full,
        limit=limit,
        now=now,
        allow_delisting=allow_delisting,
        progress=RichRunProgress(
            console, label="scraping", outcomes=("saved", "failed", "gone")
        ),
    )
    raise typer.Exit(EXIT_CODES[summary.outcome])


@app.command("parse")
def parse_command() -> None:
    """Parse every saved review page into data/parsed, and write a report.

    Exit codes: 0 finished (issues are in the report), 1 error.
    """
    settings = get_settings()
    now = datetime.now(UTC)
    console = Console(stderr=True)
    configure_logging(
        settings.log_level,
        run_log_path(settings.data_dir, "parse", now),
        console=console,
    )
    progress = RichRunProgress(
        console, label="parsing", outcomes=("roasted", "green", "issue")
    )
    try:
        result = parse(settings, progress=progress)
    except Exception as exc:
        logger.debug("parse failed", exc_info=True)  # full traceback, log file only
        logger.error("parse failed: %s: %s", type(exc).__name__, exc, extra=FILE_ONLY)
        typer.echo(f"parse failed: {exc}", err=True)  # the terminal's one copy
        raise typer.Exit(EXIT_ERROR) from None
    typer.echo(f"report: {result.report_location}")


@app.command("clean")
def clean_command() -> None:
    """Clean the parsed reviews into typed tables in data/clean, with a report.

    Values that need a look are listed in the report, and a warning says when
    any are new since the last run. Exit codes: 0 finished, 1 error (including
    a table breaking its contract, or no parsed data yet).
    """
    settings = get_settings()
    now = datetime.now(UTC)
    configure_logging(
        settings.log_level,
        run_log_path(settings.data_dir, "clean", now),
        console=Console(stderr=True),
    )
    try:
        result = clean(settings)
    except Exception as exc:
        logger.debug("clean failed", exc_info=True)  # full traceback, log file only
        logger.error("clean failed: %s: %s", type(exc).__name__, exc, extra=FILE_ONLY)
        typer.echo(f"clean failed: {exc}", err=True)  # the terminal's one copy
        raise typer.Exit(EXIT_ERROR) from None
    typer.echo(f"report: {result.report_location}")


roasters = typer.Typer(
    help="Settle roaster names a person decides (data/clean/roaster_review.csv).",
    no_args_is_help=True,
)
app.add_typer(roasters, name="roasters")


@roasters.command("apply")
def roasters_apply_command() -> None:
    """Turn decisions in data/clean/roaster_review.csv into hand-kept rows.

    Type same or different in the sheet's decision column (and, for same, an
    optional name to keep), then run this. It adds rows to roasters.csv and
    distinct_roasters.csv; run coffee-review clean afterwards. Nothing is written
    if any row is invalid. Exit codes: 0 finished, 1 error.
    """
    try:
        result = apply_roaster_review(get_settings())
    except (
        InvalidReviewSheetError,
        ReviewSheetMissingError,
        UnreadableReviewSheetError,
        OSError,
    ) as exc:
        typer.echo(f"roasters apply failed: {exc}", err=True)
        raise typer.Exit(EXIT_ERROR) from None
    typer.echo(result.describe())


@app.command("fx")
def fx_command() -> None:
    """Bring exchange rates in data/economic up to date (responses in data/raw).

    Asks only for days not saved yet. Needs COFFEE_REVIEW_FOREX_API__API_KEY.
    Exit codes: 0 finished, 1 error, 3 stopped by the source (refused, bad key,
    or not answering); a rerun carries on.
    """
    _economic_command("fx", fx)


@app.command("cpi")
def cpi_command() -> None:
    """Bring US CPI-U in data/economic up to date (responses in data/raw).

    Asks only for months not saved yet; no key needed. Exit codes: 0 finished,
    1 error, 3 stopped by the source (refused or not answering); a rerun carries
    on.
    """
    _economic_command("cpi", cpi)


def _economic_command(
    kind: Literal["fx", "cpi"], run: Callable[[Settings], EconomicRunResult]
) -> None:
    """Run one economic stage; the summary line is logged by the stage."""
    try:
        settings = get_settings()
    except ValidationError as exc:
        typer.echo(f"{kind} failed: {_first_message(exc)}", err=True)
        raise typer.Exit(EXIT_ERROR) from None
    configure_logging(
        settings.log_level,
        run_log_path(settings.data_dir, kind, datetime.now(UTC)),
        console=Console(stderr=True),
    )
    try:
        result = run(settings)
    except Exception as exc:
        logger.debug("%s failed", kind, exc_info=True)  # traceback, log file only
        logger.error(
            "%s failed: %s: %s", kind, type(exc).__name__, exc, extra=FILE_ONLY
        )
        typer.echo(f"{kind} failed: {exc}", err=True)  # the terminal's one copy
        raise typer.Exit(EXIT_ERROR) from None
    raise typer.Exit(EXIT_CODES[result.outcome])


def _first_message(exc: ValidationError) -> str:
    message = str(exc.errors()[0]["msg"])
    return message.removeprefix("Value error, ")
