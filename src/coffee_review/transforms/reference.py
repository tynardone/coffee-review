"""Pure functions for the reference stages: what to fetch, and building the
tidy tables from saved responses.

Nothing here reads a clock: ``today`` (a UTC date) is always passed in, so the
rules can be tested at any moment, including just after midnight UTC.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

import polars as pl

from coffee_review.models.reference import CPI_SCHEMA, FX_SCHEMA

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Sequence

    from coffee_review.models.reference import CpiParsed, FxParsed


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
    """Every 1st from ``start`` whose UTC day is over (``day < today``).

    On the 1st itself, that day's rates are still changing, so it waits until
    tomorrow.
    """
    return _firsts(start, today)


def fx_missing(wanted: Sequence[date], have: Collection[date]) -> list[date]:
    """The wanted days with no saved response, in order."""
    return [day for day in wanted if day not in have]


def build_fx(parsed: Iterable[FxParsed]) -> pl.DataFrame:
    """The ``fx`` table from parsed responses, sorted by day then currency. A
    day the source had no data for simply has no rows."""
    rows = [
        (rate.day, rate.currency, rate.units_per_usd)
        for response in parsed
        for rate in response.rates
    ]
    return pl.DataFrame(rows, schema=FX_SCHEMA, orient="row").sort("day", "currency")


# --- CPI ---------------------------------------------------------------------


def cpi_wanted_months(start: date, today: date) -> list[date]:
    """Every month from ``start`` through the month before ``today``'s: a
    month's CPI comes out during the next month at the earliest."""
    return _firsts(start, date(today.year, today.month, 1))


def plan_cpi(
    wanted: Sequence[date],
    known: Collection[date],
    asked_today: Collection[int],
    max_years: int,
) -> list[tuple[int, int]]:
    """The ``(start_year, end_year)`` requests to make, oldest first.

    ``known`` are months stored or marked unavailable by the source.
    ``asked_today`` are years a response saved today already covered: that
    answer is what the source has today, so a month not out yet is tried at
    most once a day. Missing years are grouped into runs of consecutive
    years, each split into requests of at most ``max_years``.
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
    """The ``cpi`` table from parsed responses, newest response first: each
    (month, series) takes the newest value."""
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
