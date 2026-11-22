"""Tests for coffee_review.transforms.units."""

from __future__ import annotations

import pytest

from coffee_review.transforms.lookup_tables import load_units
from coffee_review.transforms.units import find_unit, to_grams

UNITS = load_units()


@pytest.mark.parametrize(
    ("quantity", "spelling", "grams"),
    [
        (12, "ounces", 340.194),
        (1, "pound", 453.592),
        (1, "Kilogram", 1000.0),
        (250, "g.", 250.0),
    ],
)
def test_to_grams(quantity: float, spelling: str, grams: float) -> None:
    unit = UNITS[spelling.lower().strip(".")]

    assert to_grams(quantity, unit) == pytest.approx(grams, abs=0.001)


def test_counts_and_volumes_have_no_grams() -> None:
    assert to_grams(10, UNITS["capsules"]) is None
    assert to_grams(8, UNITS["fluid ounces"]) is None


@pytest.mark.parametrize(
    ("text", "name", "leftover", "close"),
    [
        ("ounces", "ounce", None, False),
        ("oz. instant", "ounce", "instant", False),
        ("ounce can", "ounce", "can", False),
        ("single-serve capsules", "capsule", None, False),
        ("realcup capsules", "capsule", "realcup", False),
        ("fluid ounces", "fluid ounce", None, False),
        ("ouncess", "ounce", None, True),
        ("capsulles", "capsule", None, True),
        ("onces", "ounce", None, False),  # reviewed and added, now exact
    ],
)
def test_find_unit(text: str, name: str, leftover: str | None, close: bool) -> None:
    found = find_unit(text, UNITS)

    assert found is not None
    assert (found.unit.name, found.leftover, found.close) == (name, leftover, close)


def test_find_unit_unknown() -> None:
    assert find_unit("six 8-ounce cans", UNITS) is None
    assert find_unit("boxed set", UNITS) is None
