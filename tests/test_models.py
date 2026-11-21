"""Tests for coffee_review.models."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from coffee_review.models import (
    FetchReason,
    RunMeasurement,
    ScrapeSummary,
    SitemapEntry,
    format_elapsed,
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
        "stopped_reason": None,
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
        stopped_reason="HTTP 429 at https://…/review/x/",
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
