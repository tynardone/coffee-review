"""Tests for coffee_review.models.clean: the clean-table contract."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

import polars as pl
import pytest

from coffee_review.models.clean import (
    CLEAN_RULES,
    GREEN_SCHEMA,
    LOG_SCHEMA,
    ROASTED_SCHEMA,
    RULE_DESCRIPTIONS,
    CleanContractError,
    validate_clean,
)

if TYPE_CHECKING:
    from coffee_review.models.clean import CleanTable

ROASTED_ROW: dict[str, object] = {
    "slug": "a",
    "url": "https://www.coffeereview.com/review/a/",
    "delisted": False,
    "sold_green": False,
    "title": "A Coffee",
    "roaster": "A Roaster",
    "roaster_url": "https://a.example/",
    "review_month": date(2020, 5, 1),
    "rating": 93,
    "roaster_city": "Bentonville",
    "roaster_region": "Arkansas",
    "roaster_country": "United States",
    "origin_country": "Ethiopia",
    "origin_region": "Guji, Oromia",
    "roast_level": "Medium-Light",
    "agtron_whole": 58,
    "agtron_ground": 77,
    "agtron_swapped": False,
    "agtron_corrected": False,
    "agtron_suspect": False,
    "price_amount": 13.5,
    "price_currency": "USD",
    "price_currency_inferred": False,
    "price_quantity": 12.0,
    "price_unit": "ounce",
    "price_grams": 340.19,
    "price_note": None,
    "aroma": 9.0,
    "acidity": 8.0,
    "body": 9.0,
    "flavor": 9.0,
    "aftertaste": 8.0,
    "with_milk": None,
    "blind_assessment": "Placeholder.",
    "notes": None,
    "bottom_line": None,
    "extra_count": 0,
}
GREEN_ROW: dict[str, object] = {
    "slug": "g",
    "url": "https://www.coffeereview.com/review/g/",
    "delisted": False,
    "title": "G Green",
    "roaster": "G Importer",
    "roaster_url": None,
    "review_month": date(2023, 4, 1),
    "rating": 94,
    "origin_country": "Taiwan",
    "origin_region": "Alishan",
    "price_amount": 700.0,
    "price_currency": "TWD",
    "price_currency_inferred": False,
    "price_quantity": 200.0,
    "price_unit": "gram",
    "price_grams": 200.0,
    "price_note": None,
    "harvest_year": 2023,
    "harvest_text": "April 2023",
    "seller": "S",
    "variety": "SL34",
    "lot_size_kg": 300.0,
    "lot_size_text": "300 kilograms",
    "processing_method": "Natural",
    "lot_id": "036",
    "blind_assessment": None,
    "notes": None,
    "bottom_line": None,
    "extra_count": 0,
}
LOG_ROW: dict[str, object] = {
    "slug": "a",
    "dataset": "roasted",
    "field": "rating",
    "rule": "not_rated",
    "raw_value": "NR",
}
SCHEMAS = {"roasted": ROASTED_SCHEMA, "green": GREEN_SCHEMA, "log": LOG_SCHEMA}
ROWS = {"roasted": ROASTED_ROW, "green": GREEN_ROW, "log": LOG_ROW}


def frame(table: str, *rows: dict[str, object]) -> pl.DataFrame:
    return pl.DataFrame(list(rows) or [ROWS[table]], schema=SCHEMAS[table])


def changed(table: str, **fields: object) -> pl.DataFrame:
    return frame(table, ROWS[table] | fields)


@pytest.mark.parametrize("table", ["roasted", "green", "log"])
def test_valid_frames_pass(table: CleanTable) -> None:
    valid = frame(table)

    assert validate_clean(valid, table).equals(valid)


@pytest.mark.parametrize(
    ("table", "bad"),
    [
        pytest.param(
            "roasted", frame("roasted", ROASTED_ROW, ROASTED_ROW), id="dup-slug"
        ),
        pytest.param(
            "roasted", changed("roasted", review_month=date(2020, 5, 2)), id="not-1st"
        ),
        pytest.param("roasted", changed("roasted", rating=101), id="rating-101"),
        pytest.param("roasted", changed("roasted", acidity=0.0), id="acidity-0"),
        pytest.param("roasted", changed("roasted", agtron_whole=0), id="agtron-0"),
        pytest.param("green", changed("green", price_currency="US"), id="currency"),
        pytest.param("log", changed("log", rule="made_up"), id="rule"),
        pytest.param("roasted", frame("roasted").drop("notes"), id="missing-col"),
        pytest.param(
            "roasted", frame("roasted").with_columns(pl.lit(1).alias("x")), id="extra"
        ),
    ],
)
def test_each_violation_fails(table: CleanTable, bad: pl.DataFrame) -> None:
    with pytest.raises(CleanContractError):
        validate_clean(bad, table)


def test_failure_lists_every_check() -> None:
    bad = changed("roasted", rating=101, agtron_whole=0)

    with pytest.raises(CleanContractError) as error:
        validate_clean(bad, "roasted")

    assert "rating" in str(error.value)
    assert "agtron_whole" in str(error.value)


def test_clean_rules_listed() -> None:
    assert "swapped_2002_2003" in CLEAN_RULES
    assert "typo_corrected" in CLEAN_RULES
    assert len(CLEAN_RULES) == 25


def test_every_rule_is_described() -> None:
    assert set(RULE_DESCRIPTIONS) == set(CLEAN_RULES)
    assert all(RULE_DESCRIPTIONS.values())
