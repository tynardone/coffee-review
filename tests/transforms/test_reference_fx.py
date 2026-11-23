"""Tests for the FX half of coffee_review.transforms.reference."""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import polars as pl
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
            FxRate(day=day, currency=code, units_per_usd=value)
            for code, value in rates.items()
        ),
    )


def gap(day: date) -> FxParsed:
    return FxParsed(day=day, rates=(), gap="not_available")


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
    assert fx_wanted_days(date(2026, 9, 1), date(2020, 1, 1)) == []


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


def test_build_reads_rates_and_source() -> None:
    fx, gaps = build_fx(
        [("s", day_with(date(2020, 1, 1), {"USD": 1, "TWD": 30.0}))], "s"
    )

    assert fx.rows() == [
        (date(2020, 1, 1), "TWD", 30.0, "s"),
        (date(2020, 1, 1), "USD", 1.0, "s"),
    ]
    assert gaps.is_empty()


def test_build_prefers_the_configured_source() -> None:
    day = date(2020, 1, 1)
    parsed = [("a", day_with(day, {"TWD": 30.0})), ("b", day_with(day, {"TWD": 31.0}))]

    fx, _ = build_fx(parsed, "b")

    assert fx.select("units_per_usd", "source").rows() == [(31.0, "b")]


def test_build_fills_from_another_source() -> None:
    parsed = [
        ("a", day_with(date(2020, 1, 1), {"TWD": 30.0})),
        ("b", day_with(date(2020, 2, 1), {"TWD": 31.0})),
    ]

    fx, _ = build_fx(parsed, "b")

    assert fx.select("day", "source").rows() == [
        (date(2020, 1, 1), "a"),
        (date(2020, 2, 1), "b"),
    ]


def test_build_other_sources_alphabetical() -> None:
    day = date(2020, 1, 1)
    parsed = [("z", day_with(day, {"TWD": 9.0})), ("m", day_with(day, {"TWD": 8.0}))]

    fx, _ = build_fx(parsed, "configured")

    assert fx["source"].to_list() == ["m"]


def test_build_gap_row() -> None:
    fx, gaps = build_fx([("s", gap(date(2020, 1, 1)))], "s")

    assert fx.is_empty()
    assert gaps.rows() == [(date(2020, 1, 1), "s", "not_available")]


def test_another_sources_rates_fill_a_preferred_gap() -> None:
    day = date(2020, 1, 1)

    fx, gaps = build_fx([("s", gap(day)), ("o", day_with(day, {"TWD": 30.0}))], "s")

    assert fx.select("currency", "source").rows() == [("TWD", "o")]
    assert gaps.is_empty()  # a gap row always means "no value in fx"


def test_gap_from_the_preferred_source_when_nobody_has_rates() -> None:
    day = date(2020, 1, 1)

    _, gaps = build_fx([("o", gap(day)), ("s", gap(day))], "s")

    assert gaps.select("source").rows() == [("s",)]


def test_vanished_currency_is_only_absent() -> None:
    fx, gaps = build_fx(
        [
            ("s", day_with(date(2023, 1, 1), {"USD": 1})),
            ("s", day_with(date(2022, 12, 1), {"USD": 1, "HRK": 7.1})),
        ],
        "s",
    )

    assert fx.filter(pl.col("currency") == "HRK")["day"].to_list() == [
        date(2022, 12, 1)
    ]
    assert fx["day"].to_list() == [
        date(2022, 12, 1),
        date(2022, 12, 1),
        date(2023, 1, 1),
    ]
    assert gaps.is_empty()


def test_built_tables_meet_their_contracts() -> None:
    fx, gaps = build_fx(
        [
            ("s", day_with(date(2020, 1, 1), {"USD": 1, "TWD": 30.0})),
            ("s", gap(date(2020, 2, 1))),
        ],
        "s",
    )

    validate_reference(fx, "fx")
    validate_reference(gaps, "fx_gaps")


def test_build_nothing_gives_empty_tables_with_schemas() -> None:
    fx, gaps = build_fx([], "s")

    validate_reference(fx, "fx")
    validate_reference(gaps, "fx_gaps")
    assert fx.is_empty() and gaps.is_empty()
