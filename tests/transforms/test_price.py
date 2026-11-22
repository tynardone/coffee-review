"""Tests for coffee_review.transforms.price."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from coffee_review.transforms.lookup_tables import (
    load_currencies,
    load_roaster_currencies,
    load_units,
)
from coffee_review.transforms.price import (
    PriceParts,
    clean_price,
    resolve_currency,
    split_price,
)
from tests.transforms.helpers import run

CURRENCIES = load_currencies()
BY_ROASTER = load_roaster_currencies()
UNITS = load_units()


def price_row(
    text: str, roaster_country: str | None = "United States"
) -> dict[str, object]:
    result = run(
        lambda f: clean_price(f, CURRENCIES, BY_ROASTER, UNITS),
        "price",
        [text],
        roaster_country=[roaster_country],
    )
    return result.row(0, named=True)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("$13.50/12 ounces", PriceParts(13.5, "$", 12.0, "ounces", None)),
        ("NT $700/200 grams", PriceParts(700.0, "NT $", 200.0, "grams", None)),
        ("CAD $14.00/16 ounces", PriceParts(14.0, "CAD $", 16.0, "ounces", None)),
        ("$13.45/16 oz.", PriceParts(13.45, "$", 16.0, "oz.", None)),
        ("$9.99/10 capsules", PriceParts(9.99, "$", 10.0, "capsules", None)),
        ("$54.00/1 Kilogram", PriceParts(54.0, "$", 1.0, "Kilogram", None)),
        ("$4.99/12-ounce can", PriceParts(4.99, "$", 12.0, "ounce can", None)),
    ],
)
def test_split_common_shapes(text: str, expected: PriceParts) -> None:
    assert split_price(text) == expected


def test_split_notes() -> None:
    assert split_price("NT $450/250 grams (8.8 oz.)") == PriceParts(
        450.0, "NT $", 250.0, "grams", "8.8 oz."
    )
    assert split_price("$40.00/12 ounces (sold only as a two-pack)") == PriceParts(
        40.0, "$", 12.0, "ounces", "sold only as a two-pack"
    )
    assert split_price("$22.00/12 ounces; $50.00/5 pounds") == PriceParts(
        22.0, "$", 12.0, "ounces", "$50.00/5 pounds"
    )


@pytest.mark.parametrize(
    ("text", "amount", "symbol", "quantity", "unit"),
    [
        ("9.9 Euros/225 grams", 9.9, "Euros", 225.0, "grams"),
        ("$450 NTD/100 grams", 450.0, "$ NTD", 100.0, "grams"),
        ("£6.50 for 250g", 6.5, "£", 250.0, "g"),
        ("Price: $23.25/12 ounces", 23.25, "$", 12.0, "ounces"),
        ("Kč 595/500 grams", 595.0, "Kč", 500.0, "grams"),
        ("619 Kč/227 grams", 619.0, "Kč", 227.0, "grams"),
    ],
)
def test_split_other_forms(
    text: str, amount: float, symbol: str, quantity: float, unit: str
) -> None:
    parts = split_price(text)

    assert parts is not None
    assert (parts.amount, parts.symbol, parts.quantity, parts.unit_text) == (
        amount,
        symbol,
        quantity,
        unit,
    )


def test_amount_separators() -> None:
    yen = split_price("¥ 2,420/200 grams")
    kip = price_row("$35,000 LAK/250 grams")

    assert yen is not None
    assert yen.amount == 2420.0
    assert (kip["price_amount"], kip["price_currency"]) == (35000.0, "LAK")


@pytest.mark.parametrize(
    ("symbol", "country", "expected"),
    [
        ("$", "United States", ("USD", False, ())),
        ("$", "Canada", ("CAD", True, ("currency_from_roaster",))),
        ("$", "Taiwan", ("USD", False, ())),
        ("$", None, ("USD", False, ())),
        ("¥", "Japan", ("JPY", True, ("currency_from_roaster",))),
        ("¥", "China", ("CNY", True, ("currency_from_roaster",))),
        ("¥", "Taiwan", (None, False, ("ambiguous_currency",))),
        ("¥", None, (None, False, ("ambiguous_currency",))),
        ("E", "Netherlands", ("EUR", True, ("currency_from_roaster",))),
        ("pesos", "Mexico", ("MXN", True, ("currency_from_roaster",))),
        ("NT $", "Taiwan", ("TWD", False, ())),
        ("CAD $", "Canada", ("CAD", False, ())),
        ("$ NTD", "Taiwan", ("TWD", False, ())),
        ("GTQ", "Guatemala", ("GTQ", False, ())),
        ("ZZZ", None, (None, False, ("unknown_currency",))),
        ("#", "United States", (None, False, ("unknown_currency",))),
        (None, "United States", (None, False, ("unknown_currency",))),
    ],
)
def test_resolve_currency(
    symbol: str | None, country: str | None, expected: tuple[object, ...]
) -> None:
    assert resolve_currency(symbol, country, CURRENCIES, BY_ROASTER) == expected


@pytest.mark.parametrize(
    "text",
    [
        "NA",
        "Not Available",
        "Not available.",
        "See website for more information",
        "See review note",
        "NA (available in store only)",
    ],
)
def test_not_a_price(text: str) -> None:
    row = price_row(text)

    assert row["price_amount"] is None
    assert row["price_currency"] is None
    assert row["_rule_price"] == ["not_a_price"]


def test_bare_amount() -> None:
    row = price_row("$15.00")

    assert (row["price_amount"], row["price_currency"], row["price_quantity"]) == (
        15.0,
        "USD",
        None,
    )
    assert row["_rule_price"] == ["no_quantity"]


def test_price_columns() -> None:
    row = price_row("$13.50/12 ounces")

    assert row["price_unit"] == "ounce"
    assert row["price_grams"] == pytest.approx(340.19, abs=0.01)
    assert row["price_currency_inferred"] is False
    assert row["_rule_price"] == []


def test_counts_have_no_grams() -> None:
    row = price_row("$9.99/10 capsules")

    assert (row["price_unit"], row["price_quantity"], row["price_grams"]) == (
        "capsule",
        10.0,
        None,
    )


def test_unit_leftover_goes_to_note() -> None:
    row = price_row("$12.95/12 oz. instant")

    assert (row["price_unit"], row["price_note"]) == ("ounce", "instant")


def test_missing_quantity_is_one() -> None:
    assert price_row("$8.99/can")["price_quantity"] == 1.0


def test_unknown_unit() -> None:
    row = price_row("$24.00/six 8-ounce cans")

    assert (row["price_amount"], row["price_unit"], row["price_quantity"]) == (
        24.0,
        None,
        None,
    )
    assert row["_rule_price"] == ["unknown_unit"]


def test_close_spelt_unit_is_flagged() -> None:
    row = price_row("$18.00/12 onces")

    assert row["price_unit"] == "ounce"
    assert row["_rule_price"] == ["close_spelling"]


def test_inferred_currency_flag() -> None:
    row = price_row("$13.99/12 ounces", roaster_country="Canada")

    assert (row["price_currency"], row["price_currency_inferred"]) == ("CAD", True)
    assert row["_rule_price"] == ["currency_from_roaster"]


def test_unparsed_price() -> None:
    row = price_row("$19..75/12 ounces")

    assert row["price_amount"] is None
    assert row["_rule_price"] == ["unparsed"]


def test_null_price() -> None:
    row = price_row(None)  # type: ignore[arg-type]

    assert row["price_amount"] is None
    assert row["_rule_price"] == []


SYMBOLS = sorted({"$", "NT $", "CAD $", "£", "€", "KRW", "HK $", "THB"})
UNIT_SPELLINGS = sorted({"ounces", "grams", "oz", "kg", "pound", "capsules", "pods"})


@settings(max_examples=200)
@given(
    st.sampled_from(SYMBOLS),
    st.decimals(min_value="0.01", max_value="99999", places=2),
    st.integers(min_value=1, max_value=5000),
    st.sampled_from(UNIT_SPELLINGS),
)
def test_price_round_trip(
    symbol: str, amount: object, quantity: int, unit: str
) -> None:
    text = f"{symbol}{float(str(amount)):,.2f}/{quantity} {unit}"

    parts = split_price(text)

    assert parts is not None
    assert parts.amount == pytest.approx(float(str(amount)))
    assert parts.quantity == quantity
    assert parts.unit_text == unit
