"""Tests for coffee_review.transforms.lots: green lot sizes."""

from __future__ import annotations

import pytest

from coffee_review.transforms.lookup_tables import load_units
from coffee_review.transforms.lots import clean_lot_size
from tests.transforms.helpers import run

UNITS = load_units()


@pytest.mark.parametrize(
    ("text", "kg"),
    [
        ("300 kilograms", 300.0),
        ("70 pounds", 31.751),
        ("1,900 kg (4,189 lbs)", 1900.0),
        ("20 kg.", 20.0),
        ("19,200 kilograms", 19200.0),
    ],
)
def test_lot_size_in_kg(text: str, kg: float) -> None:
    result = run(lambda f: clean_lot_size(f, UNITS), "lot_size", [text])

    assert result["lot_size_kg"].item() == pytest.approx(kg, abs=0.001)
    assert result["lot_size_text"].item() == text
    assert result["_rule_lot_size"].item().to_list() == []


@pytest.mark.parametrize(
    "text",
    ["6 boxes of 76 lbs each, nitrogen-flushed and vacuum-sealed", "10 to 20 bags"],
)
def test_lot_size_not_a_plain_weight(text: str) -> None:
    result = run(lambda f: clean_lot_size(f, UNITS), "lot_size", [text])

    assert result["lot_size_kg"].item() is None
    assert result["lot_size_text"].item() == text
    assert result["_rule_lot_size"].item().to_list() == ["unparsed"]


def test_lot_size_null() -> None:
    result = run(lambda f: clean_lot_size(f, UNITS), "lot_size", [None])

    assert result["lot_size_kg"].item() is None
    assert result["_rule_lot_size"].item().to_list() == []


def test_lot_size_not_disclosed() -> None:
    result = run(lambda f: clean_lot_size(f, UNITS), "lot_size", ["Not disclosed"])

    assert result["lot_size_kg"].item() is None
    assert result["_rule_lot_size"].item().to_list() == ["not_disclosed"]


def test_zero_lot_size_is_suspect_not_a_crash() -> None:
    result = run(lambda f: clean_lot_size(f, UNITS), "lot_size", ["0 kg"])

    assert result["lot_size_kg"].item() is None
    assert result["_rule_lot_size"].item().to_list() == ["suspect_value"]


def test_lot_size_not_disclosed_with_spaces() -> None:
    result = run(lambda f: clean_lot_size(f, UNITS), "lot_size", [" Not disclosed"])

    assert result["_rule_lot_size"].item().to_list() == ["not_disclosed"]
