"""Tests for the FX half of coffee_review.transforms.reference."""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from hypothesis import given
from hypothesis import strategies as st

from coffee_review.models.reference import FxParsed, FxRate, validate_reference
from coffee_review.transforms.reference import build_fx, fx_missing, fx_wanted_days

months = st.builds(
    date, st.integers(min_value=2005, max_value=2030), st.integers(1, 12), st.just(1)
)
days = st.dates(min_value=date(2005, 1, 1), max_value=date(2031, 1, 1))


def day_with(day: date, rates: dict[str, float]) -> FxParsed:
    return FxParsed(
        day=day,
        rates=tuple(
            FxRate(day=day, currency=c, units_per_usd=v) for c, v in rates.items()
        ),
    )


def test_wanted_days_stop_before_today() -> None:
    assert fx_wanted_days(date(2026, 9, 1), date(2026, 11, 1)) == [
        date(2026, 9, 1),
        date(2026, 10, 1),
    ]
    assert fx_wanted_days(date(2026, 9, 1), date(2026, 11, 2))[-1] == date(2026, 11, 1)


def test_wanted_days_cross_a_year() -> None:
    assert fx_wanted_days(date(2025, 11, 1), date(2026, 2, 1)) == [
        date(2025, 11, 1),
        date(2025, 12, 1),
        date(2026, 1, 1),
    ]


def test_nothing_wanted_before_start() -> None:
    assert fx_wanted_days(date(2026, 9, 1), date(2026, 9, 1)) == []


def test_utc_evening_in_new_york() -> None:
    ny_evening = datetime(2026, 11, 30, 20, 0, tzinfo=ZoneInfo("America/New_York"))
    today = ny_evening.astimezone(UTC).date()

    assert today == date(2026, 12, 1)
    assert date(2026, 12, 1) not in fx_wanted_days(date(2026, 1, 1), today)
    assert date(2026, 12, 1) in fx_wanted_days(date(2026, 1, 1), date(2026, 12, 2))


@given(start=months, today=days, have=st.sets(months))
def test_missing_is_exactly_the_unstored_past_firsts(
    start: date, today: date, have: set[date]
) -> None:
    wanted = fx_wanted_days(start, today)

    missing = fx_missing(wanted, have)

    assert all(d.day == 1 and start <= d < today and d not in have for d in missing)
    assert set(missing) | (have & set(wanted)) == set(wanted)
    assert missing == sorted(missing)


def test_build_sorts_and_meets_the_contract() -> None:
    fx = build_fx(
        [
            day_with(date(2020, 2, 1), {"USD": 1, "TWD": 31.0}),
            day_with(date(2020, 1, 1), {"USD": 1, "TWD": 30.0}),
            FxParsed(day=date(2020, 3, 1), rates=(), gap="not_available"),
        ]
    )

    assert fx.rows() == [
        (date(2020, 1, 1), "TWD", 30.0),
        (date(2020, 1, 1), "USD", 1.0),
        (date(2020, 2, 1), "TWD", 31.0),
        (date(2020, 2, 1), "USD", 1.0),
    ]
    validate_reference(fx, "fx")


def test_vanished_currency_is_only_absent() -> None:
    fx = build_fx(
        [
            day_with(date(2022, 12, 1), {"USD": 1, "HRK": 7.1}),
            day_with(date(2023, 1, 1), {"USD": 1}),
        ]
    )

    assert fx.filter(fx["currency"] == "HRK")["day"].to_list() == [date(2022, 12, 1)]


def test_build_nothing_gives_an_empty_table() -> None:
    validate_reference(build_fx([]), "fx")
