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

from dataclasses import dataclass
from typing import TYPE_CHECKING

import polars as pl

from coffee_review.models.clean import REVIEW_SHEET_COLUMNS, REVIEW_SHEET_SCHEMA
from coffee_review.storage.clean_store import open_clean_store
from coffee_review.storage.roaster_lookups import open_roaster_lookups

if TYPE_CHECKING:
    from coffee_review.config import Settings
    from coffee_review.storage.clean_store import CleanStore
    from coffee_review.storage.roaster_lookups import RoasterLookups, Row
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


# --- Applying the sheet ------------------------------------------------------------


class InvalidReviewSheetError(ValueError):
    """The review sheet has rows ``apply`` can't use: fix them and run it again."""


class ReviewSheetMissingError(Exception):
    """There's no review sheet, or it has no rows: run ``coffee-review clean``."""


@dataclass(frozen=True)
class ApplyResult:
    """What ``coffee-review roasters apply`` wrote.

    Attributes:
        listed_added: Rows added to ``roasters.csv``.
        distinct_added: Rows added to ``distinct_roasters.csv``.
        undecided: Sheet rows with no decision yet.
    """

    listed_added: tuple[Row, ...]
    distinct_added: tuple[Row, ...]
    undecided: int

    def describe(self) -> str:
        """One line for the terminal."""
        listed = len(self.listed_added)
        return (
            f"added {listed} row{'' if listed == 1 else 's'} to roasters.csv and"
            f" {len(self.distinct_added)} to distinct_roasters.csv;"
            f" {self.undecided} rows still undecided."
            " Run `coffee-review clean` to use them."
        )


def decisions_from_sheet(sheet: pl.DataFrame) -> tuple[list[Row], list[Row]]:
    """The ``roasters.csv`` and ``distinct_roasters.csv`` rows a sheet asks for.

    ``same`` and ``different`` are read whatever their case or spacing; a blank
    decision is skipped.

    Args:
        sheet: The review sheet, as ``CleanStore.read_roaster_review`` gives it.

    Returns:
        Rows for ``roasters.csv`` (spelling, name) and for
        ``distinct_roasters.csv`` (spelling, candidate).

    Raises:
        InvalidReviewSheetError: Naming every row with an unknown decision, a
            blank spelling or candidate, or a spelling given two names.
    """
    listed: list[Row] = []
    distinct: list[Row] = []
    problems: list[str] = []
    names: dict[str, str] = {}
    for number, row in enumerate(sheet.iter_rows(named=True), start=2):  # 1 = header
        decision = row["decision"].strip().lower()
        spelling, candidate = row["spelling"].strip(), row["candidate"].strip()
        where = f"row {number} ({spelling or '?'})"
        if not decision:
            continue
        if decision not in ("same", "different"):
            problems.append(
                f"{where}: decision {row['decision']!r} isn't same or different"
            )
            continue
        if not spelling or not candidate:
            problems.append(f"{where}: spelling and candidate can't be blank")
            continue
        if decision == "different":
            distinct.append((spelling, candidate))
            continue
        name = row["name"].strip() or candidate
        if names.setdefault(spelling, name) != name:
            problems.append(
                f"{where}: two names for one spelling ({names[spelling]!r}, {name!r})"
            )
            continue
        listed.append((spelling, name))
    if problems:
        raise InvalidReviewSheetError(
            "the review sheet has rows apply can't use; nothing was written:\n  "
            + "\n  ".join(problems)
        )
    return listed, distinct


def apply_roaster_review(settings: Settings) -> ApplyResult:
    """Apply the review sheet with the real stores."""
    clean = open_clean_store(settings)
    try:
        return run_apply(clean=clean, lookups=open_roaster_lookups())
    finally:
        clean.close()


def run_apply(*, clean: CleanStore, lookups: RoasterLookups) -> ApplyResult:
    """Turn the review sheet's decisions into rows of the two roaster CSVs.

    Every row is checked before anything is written; rows the CSVs already have
    are skipped, so running it twice changes nothing.

    Args:
        clean: Where the review sheet is.
        lookups: The two roaster CSVs.

    Returns:
        The rows added, and how many sheet rows are still undecided.

    Raises:
        ReviewSheetMissingError: If there's no review sheet yet.
        InvalidReviewSheetError: If any row can't be used.
    """
    sheet = clean.read_roaster_review()
    if sheet.is_empty():
        msg = (
            f"the roaster review sheet at {clean.roaster_review_location()} is"
            " missing or has no rows: run coffee-review clean first"
        )
        raise ReviewSheetMissingError(msg)
    listed, distinct = decisions_from_sheet(sheet)
    listed_added, distinct_added = lookups.add(listed, distinct)
    undecided = sheet.filter(pl.col("decision").str.strip_chars() == "").height
    return ApplyResult(tuple(listed_added), tuple(distinct_added), undecided)
