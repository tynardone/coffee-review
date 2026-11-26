"""The roaster review sheet: deciding roaster pairs in a spreadsheet.

Each clean run writes ``data/clean/roaster_review.csv``: one row per roaster
pair waiting for a person, with both sides' evidence (review counts, places,
first and last review) and an empty ``decision`` column. A decision typed in
the sheet but not yet applied is carried over to the next run's sheet.

``coffee-review roasters apply`` then turns each decision into a row of the
hand-kept CSVs, which stay the only record of what a person decided:

- ``same`` → ``roasters.csv``: ``spelling,name`` (``name`` defaults to the
  candidate);
- ``different`` → ``distinct_roasters.csv``: ``spelling,candidate``.

How roaster names are grouped in the first place:
``docs/explainers/roaster-names.md``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl

from coffee_review.models.clean import REVIEW_SHEET_COLUMNS, REVIEW_SHEET_SCHEMA

if TYPE_CHECKING:
    from coffee_review.transforms.roasters import NameMap

_KEY = ("kind", "spelling", "candidate")
_STAT_COLUMNS = ("reviews", "places", "first", "last")


def build_review_sheet(
    roasted: pl.DataFrame,
    green: pl.DataFrame,
    names: NameMap,
    previous: pl.DataFrame,
) -> pl.DataFrame:
    """One row per roaster pair a person should decide, with both sides' evidence.

    ``possible_match`` rows are pairs that weren't merged, one row per
    candidate; ``merged`` rows are automatic close-spelling merges, to check.
    Decisions in ``previous`` are kept for pairs still listed.

    Args:
        roasted: The cleaned roasted reviews, before the final column select:
            ``raw_roaster``, ``roaster``, ``review_month``, ``roaster_city``,
            ``roaster_country``.
        green: The cleaned green reviews (no location columns).
        names: The roaster name map the run applied.
        previous: The last sheet, perhaps with decisions typed in.

    Returns:
        The sheet, every column text, sorted by kind, spelling and candidate.
    """
    pairs = _pairs(names)
    if pairs.is_empty():
        return pl.DataFrame(schema=REVIEW_SHEET_SCHEMA)
    rows = _review_rows(roasted, green)
    spelling_stats = _stats(rows, "raw_roaster", prefix="")
    candidate_stats = _stats(rows, "roaster", prefix="candidate_")
    decided = previous.select(*_KEY, "decision", "name")
    return (
        pairs.join(
            spelling_stats, left_on="spelling", right_on="raw_roaster", how="left"
        )
        .join(candidate_stats, left_on="candidate", right_on="roaster", how="left")
        .join(decided, on=list(_KEY), how="left")
        .select(pl.col(c).cast(pl.String).fill_null("") for c in REVIEW_SHEET_COLUMNS)
        .sort(list(_KEY))
    )


def _pairs(names: NameMap) -> pl.DataFrame:
    """(kind, spelling, candidate) for every pair a person should look at."""
    rows = [
        ("possible_match", spelling, candidate)
        for spelling, decision in names.decisions.items()
        if "possible_match" in decision.rules
        for candidate in decision.candidates
    ] + [
        ("merged", spelling, decision.name)
        for spelling, decision in names.decisions.items()
        if "close_spelling" in decision.rules
    ]
    return pl.DataFrame(rows, schema=dict.fromkeys(_KEY, pl.String), orient="row")


def _review_rows(roasted: pl.DataFrame, green: pl.DataFrame) -> pl.DataFrame:
    """Every review's spelling, clean roaster, month and place, from both tables."""
    columns = (
        "raw_roaster",
        "roaster",
        "review_month",
        "roaster_city",
        "roaster_country",
    )
    frames = [
        frame.select(
            pl.col(name)
            if name in frame.columns
            else pl.lit(None, pl.String).alias(name)
            for name in columns
        )
        for frame in (roasted, green)
    ]
    city, country = pl.col("roaster_city"), pl.col("roaster_country")
    place = (
        pl.when(country.is_null())
        .then(None)
        .when(city.is_null() | (city == ""))
        .then(country)
        .otherwise(pl.concat_str(city, pl.lit(", "), country))
    )
    return pl.concat(frames).with_columns(place.alias("place"))


def _stats(rows: pl.DataFrame, by: str, *, prefix: str) -> pl.DataFrame:
    """Review count, places, first and last month per value of ``by``."""
    month = pl.col("review_month")
    return rows.group_by(by).agg(
        pl.len().alias(f"{prefix}reviews"),
        pl.col("place")
        .drop_nulls()
        .unique()
        .sort()
        .str.join("; ")
        .alias(f"{prefix}places"),
        month.min().dt.strftime("%Y-%m").alias(f"{prefix}first"),
        month.max().dt.strftime("%Y-%m").alias(f"{prefix}last"),
    )
