"""Tests for coffee_review.models.common: slugs, UTC datetimes, the frozen base."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from coffee_review.models.common import format_elapsed
from coffee_review.models.scrape import SitemapEntry

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


@pytest.mark.parametrize(
    ("seconds", "text"),
    [(45, "45s"), (1925, "32m 05s"), (3723, "1h 02m 03s"), (0.4, "0s")],
)
def test_format_elapsed(seconds: float, text: str) -> None:
    assert format_elapsed(seconds) == text
