"""Pure functions for the economic stages.

What to fetch, and building the tidy tables from saved responses.

Nothing here reads a clock: ``today`` (a UTC date) is always passed in, so the
rules can be tested at any moment, including just after midnight UTC.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

import polars as pl

from coffee_review.models.economic import CPI_SCHEMA, FX_SCHEMA

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Sequence

    from coffee_review.models.economic import CpiParsed, FxParsed


def next_month(month: date) -> date:
    """The 1st of the month after ``month``."""
    if month.month == 12:
        return date(month.year + 1, 1, 1)
    return date(month.year, month.month + 1, 1)


def _firsts(start: date, before: date) -> list[date]:
    """Every 1st from ``start``'s month up to, not including, ``before``."""
    firsts: list[date] = []
    month = date(start.year, start.month, 1)
    while month < before:
        firsts.append(month)
        month = next_month(month)
    return firsts


# --- Exchange rates ----------------------------------------------------------


def fx_wanted_days(start: date, today: date) -> list[date]:
    """List the days the FX table should have.

    Every 1st from ``start`` whose UTC day is over: on the 1st itself, that
    day's rates are still changing, so it waits until tomorrow.

    Args:
        start: The first 1st wanted.
        today: Today's UTC date.

    Returns:
        The days, oldest first.
    """
    return _firsts(start, today)


def fx_missing(wanted: Sequence[date], have: Collection[date]) -> list[date]:
    """List the wanted days with no saved response.

    Args:
        wanted: The days the table should have, in order.
        have: Days with a saved response.

    Returns:
        The missing days, in the order of ``wanted``.
    """
    return [day for day in wanted if day not in have]


def build_fx(parsed: Iterable[FxParsed]) -> pl.DataFrame:
    """Build the ``fx`` table from parsed responses.

    A day the source had no data for simply has no rows.

    Args:
        parsed: One parsed response per day.

    Returns:
        The table, sorted by day then currency.
    """
    rows = [
        (rate.day, rate.currency, rate.units_per_usd)
        for response in parsed
        for rate in response.rates
    ]
    return pl.DataFrame(rows, schema=FX_SCHEMA, orient="row").sort("day", "currency")


# --- CPI ---------------------------------------------------------------------


def cpi_wanted_months(start: date, today: date) -> list[date]:
    """List the months the CPI table should have.

    A month's CPI comes out during the next month at the earliest, so the
    list stops at the month before ``today``'s.

    Args:
        start: The first month wanted.
        today: Today's UTC date.

    Returns:
        The 1st of every wanted month, oldest first.
    """
    return _firsts(start, date(today.year, today.month, 1))


def plan_cpi(
    wanted: Sequence[date],
    known: Collection[date],
    asked_today: Collection[int],
    max_years: int,
) -> list[tuple[int, int]]:
    """Work out which CPI requests to make, oldest first.

    Missing years are grouped into runs of consecutive years, each split into
    requests of at most ``max_years``.

    Args:
        wanted: Every month the table should have.
        known: Months already stored, or marked unavailable by the source.
        asked_today: Years a response saved today already covered. That answer
            is what the source has today, so they aren't asked again until
            tomorrow.
        max_years: The most years one request may cover.

    Returns:
        ``(start_year, end_year)`` pairs, oldest first.
    """
    years = sorted(
        {m.year for m in wanted if m not in known and m.year not in asked_today}
    )
    requests: list[tuple[int, int]] = []
    for run in _consecutive_runs(years):
        for i in range(0, len(run), max_years):
            chunk = run[i : i + max_years]
            requests.append((chunk[0], chunk[-1]))
    return requests


def build_cpi(newest_first: Iterable[CpiParsed]) -> pl.DataFrame:
    """Build the ``cpi`` table from parsed responses.

    Args:
        newest_first: Parsed responses, newest first; each (month, series)
            takes the first value found.

    Returns:
        The table, sorted by series then month.
    """
    chosen: dict[tuple[date, str], float] = {}
    for parsed in newest_first:
        for m in parsed.months:
            chosen.setdefault((m.month, m.series), m.index_value)
    rows = [(month, series, value) for (month, series), value in chosen.items()]
    return pl.DataFrame(rows, schema=CPI_SCHEMA, orient="row").sort("series", "month")


def _consecutive_runs(years: Sequence[int]) -> list[list[int]]:
    runs: list[list[int]] = []
    for year in years:
        if runs and runs[-1][-1] == year - 1:
            runs[-1].append(year)
        else:
            runs.append([year])
    return runs
