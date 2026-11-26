"""The clean stage: parsed store → transforms → contract → report → clean store.

``run_clean`` does the work with both stores passed in, so tests can hand it
fakes. ``clean`` is the composition root that opens the real ones. Like the
other stages, this module never sees how anything is stored.

For each dataset, the parsed text columns are renamed ``raw_<field>``, run
through the transforms in order, checked (no value lost without a rule, then
the contract), and only then written. If any check fails, nothing is written:
the previous clean tables stay as they were.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import polars as pl

from coffee_review.config import CleanSettings
from coffee_review.models.clean import GREEN_SCHEMA, ROASTED_SCHEMA, validate_clean
from coffee_review.pipeline.clean_report import build_clean_report, render_clean_report
from coffee_review.storage.clean_store import open_clean_store
from coffee_review.storage.parsed_store import open_parsed_store
from coffee_review.transforms.dates import clean_harvest, clean_review_month
from coffee_review.transforms.lookup_tables import (
    load_currencies,
    load_distinct_roasters,
    load_listed_roasters,
    load_places,
    load_roaster_currencies,
    load_units,
)
from coffee_review.transforms.lots import clean_lot_size
from coffee_review.transforms.places import clean_origin, clean_roaster_location
from coffee_review.transforms.price import clean_price
from coffee_review.transforms.roast import clean_agtron, clean_roast_level
from coffee_review.transforms.roasters import (
    NameMap,
    Thresholds,
    build_name_map,
    clean_roaster,
    roaster_spellings,
)
from coffee_review.transforms.rules import (
    CLEANED_FIELDS,
    extract_log,
    raw,
    unlogged_nulls,
)
from coffee_review.transforms.scores import clean_rating, clean_scores
from coffee_review.transforms.text import clean_text_sections

if TYPE_CHECKING:
    from collections.abc import Callable

    from coffee_review.config import Settings
    from coffee_review.models.parsed import Dataset
    from coffee_review.pipeline.clean_report import CleanReport
    from coffee_review.storage.clean_store import CleanStore
    from coffee_review.storage.parsed_store import ParsedStore

logger = logging.getLogger(__name__)

ROASTED_SCORES = ("aroma", "acidity", "body", "flavor", "aftertaste", "with_milk")
MAX_UNLOGGED_SHOWN = 10


class UnloggedNullsError(Exception):
    """A transform turned a value into null without logging a rule: a bug."""


@dataclass(frozen=True)
class CleanRunResult:
    """What a clean run produced.

    Attributes:
        report: The clean report's contents.
        elapsed_seconds: How long the run took.
        report_location: Where the Markdown report was written.
    """

    report: CleanReport
    elapsed_seconds: float
    report_location: str


def clean(settings: Settings) -> CleanRunResult:
    """Clean every parsed review with the real stores."""
    parsed = open_parsed_store(settings)
    try:
        clean_store = open_clean_store(settings)
        try:
            return run_clean(parsed=parsed, clean=clean_store, settings=settings.clean)
        finally:
            clean_store.close()
    finally:
        parsed.close()


def run_clean(
    *,
    parsed: ParsedStore,
    clean: CleanStore,
    settings: CleanSettings | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> CleanRunResult:
    """Clean both datasets, check them, and write them with the log and report.

    Nothing is written unless every check passes.

    Args:
        parsed: Where to read the parsed tables.
        clean: Where to write the clean tables, log and report.
        settings: The roaster-name thresholds; the defaults when None.
        clock: Seconds counter for timing the run (tests pass a fake).

    Returns:
        The report, how long the run took, and where the report went.

    Raises:
        UnloggedNullsError: If a value became null without a rule saying why.
        CleanContractError: If a table breaks its contract.
    """
    started = clock()
    roasted = _transform(parsed.read_table("roasted"), _clean_roasted)
    green = _transform(parsed.read_table("green"), _clean_green)
    # Roaster names need every spelling at once, and the roasted locations.
    names = _roaster_names(roasted, green, settings or CleanSettings())
    roasted = clean_roaster(roasted.lazy(), names).collect()
    green = clean_roaster(green.lazy(), names).collect()
    _check_nothing_lost(roasted, green)
    log = pl.concat([extract_log(roasted, "roasted"), extract_log(green, "green")])
    tables: dict[Dataset, pl.DataFrame] = {
        "roasted": roasted.select(ROASTED_SCHEMA.names()),
        "green": green.select(GREEN_SCHEMA.names()),
    }
    for dataset, table in tables.items():
        validate_clean(table, dataset)
    validate_clean(log, "log")

    report = build_clean_report(
        tables["roasted"], tables["green"], log, clean.read_previous_log(), names
    )
    clean.write(tables["roasted"], tables["green"], log, render_clean_report(report))
    elapsed = clock() - started
    logger.info(report.summary_line(elapsed))
    _warn_about_new_values(report, clean.report_location())
    return CleanRunResult(
        report=report,
        elapsed_seconds=elapsed,
        report_location=clean.report_location(),
    )


def _transform(
    parsed: pl.DataFrame, steps: Callable[[pl.LazyFrame], pl.LazyFrame]
) -> pl.DataFrame:
    """Rename the fields to clean to ``raw_<field>``, then run the steps."""
    renames = {field: raw(field) for field in CLEANED_FIELDS if field in parsed.columns}
    return steps(parsed.rename(renames).lazy()).collect()


def _roaster_names(
    roasted: pl.DataFrame, green: pl.DataFrame, settings: CleanSettings
) -> NameMap:
    thresholds = Thresholds(
        merge_at=settings.roaster_merge_at,
        review_at=settings.roaster_review_at,
        review_same_city_at=settings.roaster_review_same_city_at,
    )
    return build_name_map(
        roaster_spellings(roasted, green),
        load_listed_roasters(),
        load_distinct_roasters(),
        thresholds,
    )


def _clean_roasted(frame: pl.LazyFrame) -> pl.LazyFrame:
    places = load_places()
    frame = clean_review_month(frame)
    frame = clean_scores(clean_rating(frame), ROASTED_SCORES)
    frame = clean_roast_level(clean_agtron(frame))  # Agtron needs review_month
    frame = clean_origin(clean_roaster_location(frame, places), places)
    frame = clean_price(  # price needs roaster_country
        frame, load_currencies(), load_roaster_currencies(), load_units()
    )
    return clean_text_sections(frame)


def _clean_green(frame: pl.LazyFrame) -> pl.LazyFrame:
    # Green reviews have no roaster location: price reads a null roaster country.
    frame = frame.with_columns(pl.lit(None, dtype=pl.String).alias("roaster_country"))
    frame = clean_rating(clean_review_month(frame))
    frame = clean_origin(frame, load_places())
    frame = clean_price(
        frame, load_currencies(), load_roaster_currencies(), load_units()
    )
    frame = clean_lot_size(clean_harvest(frame), load_units())
    return clean_text_sections(frame)


def _check_nothing_lost(*frames: pl.DataFrame) -> None:
    lost = pl.concat([unlogged_nulls(frame) for frame in frames])
    if lost.is_empty():
        return
    shown = ", ".join(
        f"{slug}: {field}" for slug, field in lost.head(MAX_UNLOGGED_SHOWN).iter_rows()
    )
    msg = f"{lost.height} values became null with no rule logged ({shown})"
    raise UnloggedNullsError(msg)


def _warn_about_new_values(report: CleanReport, location: str) -> None:
    new = report.new_to_review
    if not new:
        return
    by_rule = ", ".join(
        f"{count} {rule.replace('_', ' ')}" for rule, count in new.items()
    )
    logger.warning(
        "%d new values to review in %s (%s)", sum(new.values()), location, by_rule
    )
