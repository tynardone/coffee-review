"""Tests for coffee_review.parsers.field_aliases: every name a field has had."""

from __future__ import annotations

import pytest

from coffee_review.models.parsed import GreenReview, RoastedReview
from coffee_review.parsers.field_aliases import (
    FIELD_DATASETS,
    IGNORED_HEADINGS,
    canonical_heading,
    canonical_label,
    normalise_label,
)


@pytest.mark.parametrize(
    ("raw", "field"),
    [
        ("Roast Level:", "roast_level"),
        ("Roast  Level :", "roast_level"),
        ("roast level", "roast_level"),
        ("ACIDITY/STRUCTURE:", "acidity"),
    ],
    ids=["as-on-page", "extra-spaces", "no-colon-lower", "upper-case"],
)
def test_label_normalisation_variants(raw: str, field: str) -> None:
    assert canonical_label(raw) == field


def test_normalise_label() -> None:
    assert normalise_label(" Roast  Level :") == "roast level"


# Every label from the spec's alias table, as it appears on pages.
SPEC_LABELS = [
    ("Review Date", "review_date"),
    ("Roaster Location", "roaster_location"),
    ("Coffee Origin", "coffee_origin"),
    ("Roast Level", "roast_level"),
    ("Agtron", "agtron"),
    ("Est. Price", "price"),
    ("Estimated Price", "price"),
    ("Aroma", "aroma"),
    ("Acidity/Structure", "acidity"),
    ("Acidity", "acidity"),
    ("Body", "body"),
    ("Flavor", "flavor"),
    ("Aftertaste", "aftertaste"),
    ("With Milk", "with_milk"),
    ("Harvested", "harvested"),
    ("Seller", "seller"),
    ("Variety", "variety"),
    ("Lot Size", "lot_size"),
    ("Processing Method", "processing_method"),
    ("Lot # or ID", "lot_id"),
]


@pytest.mark.parametrize(
    ("label", "field"), SPEC_LABELS, ids=[label for label, _ in SPEC_LABELS]
)
def test_every_spec_alias(label: str, field: str) -> None:
    assert canonical_label(f"{label}:") == field


def test_unknown_label_is_none() -> None:
    assert canonical_label("Mystery Label:") is None


@pytest.mark.parametrize(
    ("heading", "field"),
    [
        ("Blind Assessment", "blind_assessment"),
        ("Notes", "notes"),
        ("Bottom Line", "bottom_line"),
        ("Who Should Drink It", "bottom_line"),
        ("Panel Comments", None),
    ],
)
def test_headings(heading: str, field: str | None) -> None:
    assert canonical_heading(heading) == field


def test_ignored_headings() -> None:
    assert normalise_label("Explore Similar Coffees") in IGNORED_HEADINGS
    assert normalise_label("Primary Sidebar") in IGNORED_HEADINGS


def test_field_datasets() -> None:
    assert FIELD_DATASETS["aroma"] == {"roasted"}
    assert FIELD_DATASETS["price"] == {"roasted", "green"}
    assert FIELD_DATASETS["seller"] == {"green"}


def test_every_canonical_field_has_its_datasets() -> None:
    for field, datasets in FIELD_DATASETS.items():
        if "roasted" in datasets:
            assert field in RoastedReview.columns, field
        if "green" in datasets:
            assert field in GreenReview.columns, field
