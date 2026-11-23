"""Tests for the CPI half of coffee_review.transforms.reference."""

from __future__ import annotations

from datetime import UTC, date, datetime

from hypothesis import given
from hypothesis import strategies as st

from coffee_review.models.reference import (
    CpiMonth,
    CpiParsed,
    CpiResponse,
    validate_reference,
)
from coffee_review.transforms.reference import (
    build_cpi,
    cpi_gap_months,
    cpi_stored_months,
    cpi_wanted_months,
    plan_cpi,
)

SERIES = "CUUR0000SA0"
EARLIER = datetime(2026, 1, 2, 9, tzinfo=UTC)

type Saved = tuple[CpiResponse, CpiParsed]


def months_of(year: int) -> list[date]:
    return [date(year, m, 1) for m in range(1, 13)]


def resp(
    start: int,
    end: int,
    *,
    months: list[date],
    fetched_at: datetime = EARLIER,
    source: str = "bls",
    preliminary: frozenset[date] = frozenset(),
    value: float = 100.0,
) -> Saved:
    response = CpiResponse(
        source=source,
        series=SERIES,
        fetched_at=fetched_at,
        start_year=start,
        end_year=end,
    )
    parsed = CpiParsed(
        series=SERIES,
        months=tuple(
            CpiMonth(
                month=m, series=SERIES, index_value=value, final=m not in preliminary
            )
            for m in months
        ),
    )
    return response, parsed


def test_wanted_months_end_last_month() -> None:
    assert cpi_wanted_months(date(2026, 9, 1), date(2026, 11, 22)) == [
        date(2026, 9, 1),
        date(2026, 10, 1),
    ]


def test_wanted_months_on_the_1st() -> None:
    assert cpi_wanted_months(date(2026, 9, 1), date(2026, 11, 1))[-1] == date(
        2026, 10, 1
    )


def test_first_run_asks_in_ten_year_chunks() -> None:
    wanted = cpi_wanted_months(date(1997, 1, 1), date(2026, 11, 22))

    assert plan_cpi(wanted, [], date(2026, 11, 22), 10) == [
        (1997, 2006),
        (2007, 2016),
        (2017, 2026),
    ]


def test_october_2025_is_a_gap_and_not_asked_again() -> None:
    saved = [
        resp(2025, 2025, months=[m for m in months_of(2025) if m != date(2025, 10, 1)])
    ]

    assert cpi_gap_months(saved) == {date(2025, 10, 1)}
    wanted = cpi_wanted_months(date(2025, 1, 1), date(2026, 1, 20))
    assert plan_cpi(wanted, saved, date(2026, 1, 20), 10) == []


def test_not_out_yet_is_not_a_gap() -> None:
    saved = [resp(2026, 2026, months=months_of(2026)[:9])]

    assert cpi_gap_months(saved) == set()


def test_newest_month_not_out_yet_asked_once_a_day() -> None:
    asked = datetime(2026, 11, 5, 9, tzinfo=UTC)
    saved = [resp(2026, 2026, months=months_of(2026)[:9], fetched_at=asked)]
    wanted = cpi_wanted_months(date(2026, 1, 1), date(2026, 11, 5))

    assert plan_cpi(wanted, saved, date(2026, 11, 5), 10) == []
    assert plan_cpi(wanted, saved, date(2026, 11, 6), 10) == [(2026, 2026)]


def test_preliminary_month_is_asked_again_next_day() -> None:
    saved = [
        resp(
            2020,
            2020,
            months=months_of(2020),
            preliminary=frozenset({date(2020, 12, 1)}),
        )
    ]
    wanted = cpi_wanted_months(date(2020, 1, 1), date(2021, 1, 20))

    assert date(2020, 12, 1) not in cpi_stored_months(saved)
    assert plan_cpi(wanted, saved, date(2021, 1, 20), 10) == [(2020, 2020)]


def test_new_month_asks_only_its_year() -> None:
    saved = [
        resp(2017, 2026, months=[*months_of(2025), *months_of(2026)[:10]]),
        resp(2007, 2016, months=[m for y in range(2007, 2017) for m in months_of(y)]),
    ]
    saved += [
        resp(2017, 2024, months=[m for y in range(2017, 2025) for m in months_of(y)])
    ]
    wanted = cpi_wanted_months(date(2007, 1, 1), date(2026, 12, 20))

    assert plan_cpi(wanted, saved, date(2026, 12, 20), 10) == [(2026, 2026)]


years = st.sets(st.integers(min_value=1997, max_value=2026))


@given(stored_years=years, max_years=st.integers(min_value=1, max_value=20))
def test_plan_covers_exactly_the_missing_years_in_chunks(
    stored_years: set[int], max_years: int
) -> None:
    saved = [resp(y, y, months=months_of(y)) for y in sorted(stored_years)]
    wanted = cpi_wanted_months(date(1997, 1, 1), date(2027, 1, 10))

    chunks = plan_cpi(wanted, saved, date(2027, 1, 10), max_years)

    asked = [y for start, end in chunks for y in range(start, end + 1)]
    assert all(end - start + 1 <= max_years and start <= end for start, end in chunks)
    assert sorted(asked) == asked
    assert set(asked) == set(range(1997, 2027)) - stored_years
    assert len(asked) == len(set(asked))


def test_build_prefers_configured_then_newest() -> None:
    month = date(2020, 1, 1)
    old = datetime(2021, 1, 1, tzinfo=UTC)
    new = datetime(2022, 1, 1, tzinfo=UTC)
    saved = [
        resp(2020, 2020, months=[month], source="bls", fetched_at=old, value=1.0),
        resp(2020, 2020, months=[month], source="bls", fetched_at=new, value=2.0),
        resp(2020, 2020, months=[month], source="other", fetched_at=new, value=9.0),
    ]

    cpi, _ = build_cpi(saved, "bls")

    assert cpi.select("index_value", "source").rows() == [(2.0, "bls")]


def test_build_fills_from_another_source() -> None:
    saved = [
        resp(2020, 2020, months=[date(2020, 1, 1)], source="other"),
        resp(2020, 2020, months=[date(2020, 2, 1)], source="bls"),
    ]

    cpi, _ = build_cpi(saved, "bls")

    assert cpi.select("month", "source").rows() == [
        (date(2020, 1, 1), "other"),
        (date(2020, 2, 1), "bls"),
    ]


def test_build_keeps_a_preliminary_value() -> None:
    saved = [
        resp(
            2020,
            2020,
            months=[date(2020, 1, 1)],
            preliminary=frozenset({date(2020, 1, 1)}),
        )
    ]

    cpi, _ = build_cpi(saved, "bls")

    assert cpi["month"].to_list() == [date(2020, 1, 1)]


def test_build_gap_rows() -> None:
    saved = [
        resp(2025, 2025, months=[m for m in months_of(2025) if m != date(2025, 10, 1)])
    ]

    cpi, gaps = build_cpi(saved, "bls")

    assert gaps.rows() == [
        (
            date(2025, 10, 1),
            SERIES,
            "bls",
            "absent from a response that has later months",
        )
    ]
    assert date(2025, 10, 1) not in cpi["month"].to_list()


def test_a_month_filled_later_is_no_longer_a_gap() -> None:
    later = datetime(2026, 3, 1, tzinfo=UTC)
    saved = [
        resp(2025, 2025, months=[m for m in months_of(2025) if m != date(2025, 10, 1)]),
        resp(2025, 2025, months=months_of(2025), fetched_at=later),
    ]

    _, gaps = build_cpi(saved, "bls")

    assert gaps.is_empty()


def test_built_tables_meet_their_contracts() -> None:
    saved = [
        resp(2025, 2025, months=[m for m in months_of(2025) if m != date(2025, 10, 1)]),
        resp(2025, 2025, months=months_of(2025)[:3], source="other"),
    ]

    cpi, gaps = build_cpi(saved, "bls")

    validate_reference(cpi, "cpi")
    validate_reference(gaps, "cpi_gaps")


def test_build_nothing_gives_empty_tables_with_schemas() -> None:
    cpi, gaps = build_cpi([], "bls")

    validate_reference(cpi, "cpi")
    validate_reference(gaps, "cpi_gaps")
