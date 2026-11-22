"""Tests for coffee_review.transforms.dates."""

from __future__ import annotations

from datetime import date

from coffee_review.transforms.dates import clean_harvest, clean_review_month
from tests.transforms.helpers import run

MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]  # fmt: skip


def test_review_month_every_month_name() -> None:
    result = run(clean_review_month, "review_date", [f"{m} 2020" for m in MONTHS])

    assert result["review_month"].to_list() == [date(2020, n, 1) for n in range(1, 13)]
    assert result["_rule_review_date"].to_list() == [[]] * 12


def test_review_month_unparsed() -> None:
    result = run(clean_review_month, "review_date", ["Spring 2020", None])

    assert result["review_month"].to_list() == [None, None]
    assert result["_rule_review_date"].to_list() == [["unparsed"], []]


def test_harvest_year_from_ranges() -> None:
    values = [
        "2024",
        "April 2023",
        "January to March 2021",
        "November 2022 to March 2023",
    ]

    result = run(clean_harvest, "harvested", values)

    assert result["harvest_year"].to_list() == [2024, 2023, 2021, 2023]
    assert result["harvest_text"].to_list() == values


def test_harvest_without_year() -> None:
    result = run(clean_harvest, "harvested", ["Spring", None])

    assert result["harvest_year"].to_list() == [None, None]
    assert result["harvest_text"].to_list() == ["Spring", None]
    assert result["_rule_harvested"].to_list() == [["unparsed"], []]
