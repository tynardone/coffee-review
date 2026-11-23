"""Pure functions for the reference stages: what to fetch, and building the
tidy tables from saved responses.

Nothing here reads a clock: ``today`` (a UTC date) is always passed in, so the
rules can be tested at any moment, including just after midnight UTC.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

import polars as pl

from coffee_review.models.reference import FX_GAPS_SCHEMA, FX_SCHEMA

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence

    from coffee_review.models.reference import FxParsed


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


def _rank(source: str, preferred: str) -> tuple[bool, str]:
    """Sort key: the preferred source first, then the rest alphabetically."""
    return (source != preferred, source)
