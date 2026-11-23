"""Pure functions for the reference stages: what to fetch, and building the
tidy tables from saved responses.

Nothing here reads a clock: ``today`` (a UTC date) is always passed in, so the
rules can be tested at any moment, including just after midnight UTC.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

import polars as pl

from coffee_review.models.reference import (
    CPI_GAPS_SCHEMA,
    CPI_SCHEMA,
    FX_GAPS_SCHEMA,
    FX_SCHEMA,
)

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence

    from coffee_review.models.reference import CpiParsed, CpiResponse, FxParsed

    type SavedCpi = tuple[CpiResponse, CpiParsed]


def next_month(month: date) -> date:
    """The 1st of the month after ``month``."""
    if month.month == 12:
        return date(month.year + 1, 1, 1)
    return date(month.year, month.month + 1, 1)


# --- Exchange rates ----------------------------------------------------------


def fx_wanted_days(start: date, today: date) -> list[date]:
    """Every 1st from ``start`` whose UTC day is over (``day < today``).

    On the 1st itself, that day's rates are still changing, so it waits until
    tomorrow.
    """
    wanted: list[date] = []
    day = date(start.year, start.month, 1)
    while day < today:
        wanted.append(day)
        day = next_month(day)
    return wanted


def fx_missing(wanted: Sequence[date], have: Collection[date]) -> list[date]:
    """The wanted days with no saved response, in order."""
    return [day for day in wanted if day not in have]


def build_fx(
    parsed: Sequence[tuple[str, FxParsed]], preferred: str
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """The ``fx`` and ``fx_gaps`` tables from parsed responses, as
    ``(source, parsed)`` pairs.

    Each day comes from one response: the preferred source's if it has one,
    otherwise the alphabetically first other source's. A gap response gives
    a gap row and no rates.
    """
    chosen: dict[date, tuple[str, FxParsed]] = {}
    for source, response in sorted(parsed, key=lambda p: _rank(p[0], preferred)):
        chosen.setdefault(response.day, (source, response))

    rates: list[tuple[date, str, float, str]] = []
    gaps: list[tuple[date, str, str]] = []
    for day in sorted(chosen):
        source, response = chosen[day]
        if response.gap is not None:
            gaps.append((day, source, response.gap))
            continue
        rates += [(day, r.currency, r.units_per_usd, source) for r in response.rates]

    fx = pl.DataFrame(rates, schema=FX_SCHEMA, orient="row").sort("day", "currency")
    return fx, pl.DataFrame(gaps, schema=FX_GAPS_SCHEMA, orient="row")


# --- CPI ---------------------------------------------------------------------

GAP_SAID = "absent from a response that has later months"


def cpi_wanted_months(start: date, today: date) -> list[date]:
    """Every month from ``start`` through the month before ``today``'s: a
    month's CPI comes out during the next month at the earliest."""
    this_month = date(today.year, today.month, 1)
    wanted: list[date] = []
    month = date(start.year, start.month, 1)
    while month < this_month:
        wanted.append(month)
        month = next_month(month)
    return wanted


def cpi_gap_months(saved: Sequence[SavedCpi]) -> set[date]:
    """Months a source will never have, from one source's responses.

    A month is a gap when the source says it's unavailable, or when a
    response covered its year, has a later month, and doesn't have that one.
    A month after a response's latest is just not out yet.
    """
    gaps: set[date] = {u.month for _, parsed in saved for u in parsed.unavailable}
    for response, parsed in saved:
        present = {m.month for m in parsed.months}
        if not present:
            continue
        latest = max(present)
        for year in range(response.start_year, response.end_year + 1):
            gaps |= {
                month
                for month in (date(year, n, 1) for n in range(1, 13))
                if month < latest and month not in present
            }
    return gaps


def cpi_stored_months(saved: Sequence[SavedCpi]) -> set[date]:
    """Months with a final value (a preliminary one is asked for again)."""
    return {m.month for _, parsed in saved for m in parsed.months if m.final}


def plan_cpi(
    wanted: Sequence[date],
    saved: Sequence[SavedCpi],
    today: date,
    max_years: int,
) -> list[tuple[int, int]]:
    """The ``(start_year, end_year)`` requests to make, oldest first.

    ``saved`` is the configured source's responses for the series. Missing
    months are wanted ones neither stored nor gaps. A year a response saved
    today already covered isn't asked again today: that answer is what the
    source has today (so a month not out yet is tried once a day). Missing
    years are grouped into runs of consecutive years, each split into
    requests of at most ``max_years``.
    """
    known = cpi_stored_months(saved) | cpi_gap_months(saved)
    asked_today = {
        year
        for response, _ in saved
        if response.fetched_at.date() == today
        for year in range(response.start_year, response.end_year + 1)
    }
    years = sorted(
        {m.year for m in wanted if m not in known and m.year not in asked_today}
    )

    requests: list[tuple[int, int]] = []
    for run in _consecutive_runs(years):
        for i in range(0, len(run), max_years):
            chunk = run[i : i + max_years]
            requests.append((chunk[0], chunk[-1]))
    return requests


def build_cpi(
    saved: Sequence[SavedCpi], preferred: str
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """The ``cpi`` and ``cpi_gaps`` tables from every source's responses.

    Each (month, series) comes from the preferred source if it has a value,
    otherwise another source (alphabetically), and the newest response within
    a source. Preliminary values are kept: they're the best known so far.
    """
    ordered = sorted(saved, key=lambda s: (_rank(s[0].source, preferred), _newest(s)))
    chosen: dict[tuple[date, str], tuple[float, str]] = {}
    for response, parsed in ordered:
        for m in parsed.months:
            chosen.setdefault((m.month, m.series), (m.index_value, response.source))
    rows = [
        (month, series, value, source)
        for (month, series), (value, source) in chosen.items()
    ]
    cpi = pl.DataFrame(rows, schema=CPI_SCHEMA, orient="row").sort("series", "month")

    gaps: list[tuple[date, str, str, str]] = []
    for source, series in sorted({(r.source, r.series) for r, _ in saved}):
        own = [s for s in saved if (s[0].source, s[0].series) == (source, series)]
        valued = {m.month for _, parsed in own for m in parsed.months}
        said = {u.month: u.said for _, parsed in own for u in parsed.unavailable}
        gaps += [
            (month, series, source, said.get(month, GAP_SAID))
            for month in sorted(cpi_gap_months(own) - valued)
        ]
    return cpi, pl.DataFrame(gaps, schema=CPI_GAPS_SCHEMA, orient="row")


def _newest(saved: SavedCpi) -> float:
    """Sort key: the newest response first."""
    return -saved[0].fetched_at.timestamp()


def _consecutive_runs(years: Sequence[int]) -> list[list[int]]:
    runs: list[list[int]] = []
    for year in years:
        if runs and runs[-1][-1] == year - 1:
            runs[-1].append(year)
        else:
            runs.append([year])
    return runs


def _rank(source: str, preferred: str) -> tuple[bool, str]:
    """Sort key: the preferred source first, then the rest alphabetically."""
    return (source != preferred, source)
