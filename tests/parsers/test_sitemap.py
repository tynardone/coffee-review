"""Tests for coffee_review.parsers.sitemap."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from coffee_review.models.scrape import SitemapEntry
from coffee_review.parsers.sitemap import (
    SitemapParseError,
    dedupe_entries,
    parse_review_sitemap,
    parse_sitemap_index,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "sitemaps"
SITE = "https://www.coffeereview.com"


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def urlset(*urls: str) -> str:
    """A minimal review sitemap wrapping the given ``<url>`` elements."""
    body = "".join(urls)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{body}</urlset>"
    )


def test_index_keeps_only_review_sitemaps() -> None:
    urls = parse_sitemap_index(read_fixture("sitemap_index.xml"))

    assert urls == [f"{SITE}/review-sitemap.xml", f"{SITE}/review-sitemap10.xml"]


def test_review_sitemap_entries() -> None:
    entries = parse_review_sitemap(read_fixture("review-sitemap.xml"))

    assert len(entries) == 3
    assert entries[0] == SitemapEntry(
        slug="lauras-secret",
        url=f"{SITE}/review/lauras-secret/",
        lastmod=datetime(2026, 7, 31, 17, 39, 14, tzinfo=UTC),
    )


def test_missing_lastmod_is_none() -> None:
    entries = parse_review_sitemap(read_fixture("review-sitemap.xml"))

    assert entries[2].slug == "colombia-jesus-arbey-iquira-pink-bourbon-2"
    assert entries[2].lastmod is None


def test_non_review_urls_skipped() -> None:
    entries = parse_review_sitemap(read_fixture("review-sitemap.xml"))

    assert all("/review/" in entry.url for entry in entries)


def test_unparseable_lastmod_is_none() -> None:
    xml = urlset(
        f"<url><loc>{SITE}/review/x/</loc><lastmod>last tuesday</lastmod></url>"
    )

    assert parse_review_sitemap(xml)[0].lastmod is None


def test_invalid_slug_skipped() -> None:
    xml = urlset(
        f"<url><loc>{SITE}/review/Bad_Slug/</loc></url>",
        f"<url><loc>{SITE}/review/good-slug/</loc></url>",
    )

    assert [entry.slug for entry in parse_review_sitemap(xml)] == ["good-slug"]


def test_malformed_xml_raises() -> None:
    with pytest.raises(SitemapParseError):
        parse_review_sitemap("<urlset><url>")


def entry(slug: str, lastmod: datetime | None) -> SitemapEntry:
    return SitemapEntry(slug=slug, url=f"{SITE}/review/{slug}/", lastmod=lastmod)


def test_duplicate_slugs_keep_newest_lastmod() -> None:
    older = datetime(2026, 1, 1, tzinfo=UTC)
    newer = datetime(2026, 6, 1, tzinfo=UTC)

    result = dedupe_entries(
        [entry("a", older), entry("b", None), entry("a", newer), entry("a", None)]
    )

    assert result == [entry("a", newer), entry("b", None)]
