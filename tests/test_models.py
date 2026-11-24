"""Tests for coffee_review.models."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from coffee_review.models.common import format_elapsed
from coffee_review.models.parsed import (
    GreenReview,
    ParsedPage,
    ParseIssue,
    RoastedReview,
)
from coffee_review.models.scrape import (
    FetchReason,
    RunMeasurement,
    ScrapeSummary,
    SitemapEntry,
)

URL = "https://www.coffeereview.com/review/x/"
NOON_UTC = datetime(2026, 11, 21, 12, tzinfo=UTC)


@pytest.mark.parametrize("slug", ["kenya-karinga-4", "caf%c3%a9-blend"])
def test_slug_accepts_real_slugs(slug: str) -> None:
    assert SitemapEntry(slug=slug, url=URL, lastmod=None).slug == slug


@pytest.mark.parametrize(
    "slug",
    ["", "..", "a/b", "Kenya", "-x"],
    ids=["empty", "dotdot", "slash", "uppercase", "leading-dash"],
)
def test_slug_rejects_path_tricks(slug: str) -> None:
    with pytest.raises(ValidationError):
        SitemapEntry(slug=slug, url=URL, lastmod=None)


def test_naive_datetime_rejected() -> None:
    with pytest.raises(ValidationError):
        SitemapEntry(slug="x", url=URL, lastmod=datetime(2026, 11, 21, 12))


def test_aware_datetime_converted_to_utc() -> None:
    plus_two = timezone(timedelta(hours=2))

    entry = SitemapEntry(
        slug="x", url=URL, lastmod=datetime(2026, 11, 21, 12, tzinfo=plus_two)
    )

    assert entry.lastmod == datetime(2026, 11, 21, 10, tzinfo=UTC)
    assert entry.lastmod is not None
    assert entry.lastmod.tzinfo is UTC


def test_models_are_frozen() -> None:
    entry = SitemapEntry(slug="x", url=URL, lastmod=NOON_UTC)

    with pytest.raises(ValidationError):
        entry.slug = "y"  # type: ignore[misc]


def summary(**overrides: object) -> ScrapeSummary:
    fields: dict[str, object] = {
        "outcome": "finished",
        "fetched": {FetchReason.NEW: 3, FetchReason.CHANGED: 9},
        "unchanged": 9388,
        "failed": 0,
        "gone_slugs": [],
        "delisted": 1,
        "relisted": 0,
        "remaining": 0,
        "reason": None,
    }
    fields.update(overrides)
    return ScrapeSummary.model_validate(fields)


def test_describe_finished() -> None:
    assert summary().describe() == (
        "scrape finished: 12 fetched (3 new, 9 changed), 9,388 unchanged, "
        "0 failed, 0 gone, 1 delisted"
    )


def test_describe_stopped() -> None:
    stopped = summary(
        outcome="stopped",
        reason="HTTP 429 at https://…/review/x/",
        remaining=40,
    )

    assert stopped.describe().endswith(
        " (stopped: HTTP 429 at https://…/review/x/; 40 remaining)"
    )


def test_describe_nothing_fetched_has_no_parentheses() -> None:
    assert summary(fetched={}).describe().startswith("scrape finished: 0 fetched, ")


def measurement(**overrides: object) -> RunMeasurement:
    fields: dict[str, object] = {
        "requests": 9431,
        "elapsed_seconds": 1925,
        "mean_response_seconds": 0.52,
        "rate_per_sec": 5.0,
        "max_concurrency": 5,
    }
    fields.update(overrides)
    return RunMeasurement.model_validate(fields)


def test_describe_reports_measurements() -> None:
    lines = summary(measurement=measurement()).describe().splitlines()

    assert lines[1] == (
        "  in 32m 05s · 9,431 requests · avg 4.9/s · avg response 0.52s"
        " · rate 5.0/s, concurrency 5"
    )


def test_describe_without_requests_shows_no_response_time() -> None:
    quiet = measurement(requests=0, elapsed_seconds=0, mean_response_seconds=None)

    assert quiet.describe() == (
        "  in 0s · 0 requests · avg 0.0/s · avg response — · rate 5.0/s, concurrency 5"
    )


@pytest.mark.parametrize(
    ("seconds", "text"),
    [(45, "45s"), (1925, "32m 05s"), (3723, "1h 02m 03s"), (0.4, "0s")],
)
def test_format_elapsed(seconds: float, text: str) -> None:
    assert format_elapsed(seconds) == text


# --- parsed records ----------------------------------------------------------


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


def test_dataset_columns_match_spec() -> None:
    # Copied from the spec's "Datasets and tables" section, without slug, url,
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


def test_summary_of_an_early_ending_needs_only_outcome_and_reason() -> None:
    summary = ScrapeSummary(outcome="error", reason="no review sitemaps listed")

    assert summary.fetched == {}
    assert summary.unchanged == summary.failed == summary.remaining == 0
    assert summary.gone_slugs == []
    assert summary.describe().endswith("(error: no review sitemaps listed)")
