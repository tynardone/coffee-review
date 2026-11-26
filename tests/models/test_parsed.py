"""Tests for coffee_review.models.parsed: parsed records and issues."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from coffee_review.models.parsed import (
    GreenReview,
    ParsedPage,
    ParseIssue,
    RoastedReview,
)

URL = "https://www.coffeereview.com/review/x/"


def roasted(**overrides: object) -> RoastedReview:
    fields: dict[str, object] = {
        "slug": "x", "url": URL, "delisted": False, "sold_green": False,
    }  # fmt: skip
    fields.update(overrides)
    return RoastedReview.model_validate(fields)


def test_review_fields_default_to_none() -> None:
    review = roasted()

    assert review.title is None
    assert review.bottom_line is None
    assert review.extra == {}
    assert review.dataset == "roasted"


def test_review_rejects_unsafe_slug() -> None:
    with pytest.raises(ValidationError):
        roasted(slug="../x")


def test_roasted_review_cannot_hold_green_fields() -> None:
    with pytest.raises(ValidationError):
        roasted(lot_id="123")


def test_green_review_cannot_hold_roasted_fields() -> None:
    with pytest.raises(ValidationError):
        GreenReview(slug="x", url=URL, delisted=False, aroma="9")  # type: ignore[call-arg]


def test_dataset_columns_in_published_order() -> None:
    # The published column order (docs/data-dictionary.md), without slug, url,
    # delisted, sold_green and extra_count.
    assert RoastedReview.columns == (
        "title", "roaster", "roaster_url", "rating", "review_date",
        "roaster_location", "coffee_origin", "roast_level", "agtron", "price",
        "aroma", "acidity", "body", "flavor", "aftertaste", "with_milk",
        "blind_assessment", "notes", "bottom_line",
    )  # fmt: skip
    assert GreenReview.columns == (
        "title", "roaster", "roaster_url", "rating", "review_date",
        "coffee_origin", "price", "harvested", "seller", "variety", "lot_size",
        "processing_method", "lot_id", "blind_assessment", "notes", "bottom_line",
    )  # fmt: skip


@pytest.mark.parametrize(
    "model", [RoastedReview, GreenReview], ids=["roasted", "green"]
)
def test_columns_are_exactly_the_models_text_fields(
    model: type[RoastedReview | GreenReview],
) -> None:
    # The column order is spelled out once; this keeps it in step with the model.
    not_columns = {"slug", "url", "dataset", "delisted", "sold_green", "extra"}

    assert sorted(model.columns) == sorted(set(model.model_fields) - not_columns)


def test_roasted_and_green_share_core_fields() -> None:
    core = {"review_date", "price", "blind_assessment", "notes", "bottom_line"}

    assert core <= set(RoastedReview.columns) & set(GreenReview.columns)


def test_parsed_page_picks_the_model_from_dataset() -> None:
    page = ParsedPage.model_validate(
        {"record": {"slug": "x", "url": URL, "dataset": "green", "delisted": False}}
    )

    assert isinstance(page.record, GreenReview)


def test_parse_issue_kinds_are_checked() -> None:
    with pytest.raises(ValidationError):
        ParseIssue.model_validate({"slug": "x", "kind": "made_up", "detail": ""})
