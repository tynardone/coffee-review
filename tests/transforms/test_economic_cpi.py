"""Tests for the CPI half of coffee_review.transforms.economic."""

from __future__ import annotations

from datetime import date

from hypothesis import given
from hypothesis import strategies as st

from coffee_review.models.economic import CpiMonth, CpiParsed, validate_economic
from coffee_review.transforms.economic import build_cpi, cpi_wanted_months, plan_cpi

SERIES = "CUUR0000SA0"


def months_of(*years: int) -> list[date]:
    return [date(y, m, 1) for y in years for m in range(1, 13)]


def parsed(months: list[date], value: float = 100.0) -> CpiParsed:
    return CpiParsed(
        series=SERIES,
        months=tuple(
            CpiMonth(month=m, series=SERIES, index_value=value) for m in months
        ),
    )


def test_wanted_months_end_last_month() -> None:
    assert cpi_wanted_months(date(2026, 9, 1), date(2026, 11, 22)) == [
        date(2026, 9, 1),
        date(2026, 10, 1),
    ]
    assert cpi_wanted_months(date(2026, 9, 1), date(2026, 11, 1))[-1] == date(
        2026, 10, 1
    )


def test_first_run_asks_in_ten_year_chunks() -> None:
    wanted = cpi_wanted_months(date(1997, 1, 1), date(2026, 11, 22))

    assert plan_cpi(wanted, set(), set(), 10) == [
        (1997, 2006),
        (2007, 2016),
        (2017, 2026),
    ]


def test_known_months_not_asked() -> None:
    wanted = cpi_wanted_months(date(2025, 1, 1), date(2026, 1, 20))

    assert plan_cpi(wanted, set(months_of(2025)), set(), 10) == []


def test_a_year_asked_today_isnt_asked_again_today() -> None:
    wanted = cpi_wanted_months(date(2026, 1, 1), date(2026, 11, 5))
    known = set(months_of(2026)[:9])  # October not out yet

    assert plan_cpi(wanted, known, {2026}, 10) == []
    assert plan_cpi(wanted, known, set(), 10) == [(2026, 2026)]


@given(
    known_years=st.sets(st.integers(min_value=1997, max_value=2026)),
    max_years=st.integers(min_value=1, max_value=20),
)
def test_plan_covers_exactly_the_missing_years_in_chunks(
    known_years: set[int], max_years: int
) -> None:
    wanted = cpi_wanted_months(date(1997, 1, 1), date(2027, 1, 10))
    known = {m for m in wanted if m.year in known_years}

    chunks = plan_cpi(wanted, known, set(), max_years)

    asked = [y for start, end in chunks for y in range(start, end + 1)]
    assert all(start <= end and end - start + 1 <= max_years for start, end in chunks)
    assert asked == sorted(set(asked))
    assert set(asked) == set(range(1997, 2027)) - known_years


def test_build_takes_the_newest_value() -> None:
    month = [date(2020, 1, 1)]

    cpi = build_cpi([parsed(month, value=2.0), parsed(month, value=1.0)])

    assert cpi.rows() == [(date(2020, 1, 1), SERIES, 2.0)]
    validate_economic(cpi, "cpi")


def test_build_nothing_gives_an_empty_table() -> None:
    validate_economic(build_cpi([]), "cpi")
