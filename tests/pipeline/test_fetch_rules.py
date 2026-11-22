"""Tests for coffee_review.pipeline.fetch_rules: the scraper's rerun rules.

Every case uses a fixed time ``T`` for the last successful fetch, so each row
reads as "sitemap says X, we fetched at T, it's now Y".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from coffee_review.models import FetchReason, ManifestRecord, SitemapEntry
from coffee_review.pipeline.fetch_rules import (
    Delistings,
    delisting_exceeds_limit,
    find_delistings,
    select_reviews_to_fetch,
)

T = datetime(2026, 11, 1, 12, tzinfo=UTC)
HOUR = timedelta(hours=1)
DAY = timedelta(days=1)
SECOND = timedelta(seconds=1)


def entry(slug: str = "x", lastmod: datetime | None = T - 2 * DAY) -> SitemapEntry:
    return SitemapEntry(
        slug=slug, url=f"https://www.coffeereview.com/review/{slug}/", lastmod=lastmod
    )


def record(slug: str = "x", **overrides: Any) -> ManifestRecord:
    """A healthy manifest row: fetched at T, unchanged since two days before."""
    fields: dict[str, Any] = {
        "slug": slug,
        "url": f"https://www.coffeereview.com/review/{slug}/",
        "lastmod": T - 2 * DAY,
        "fetched_at": T,
        "last_attempt_at": T,
        "status": "ok",
        "last_error": None,
        "lookback_done": False,
        "first_seen_at": T - 30 * DAY,
        "delisted_at": None,
    }
    fields.update(overrides)
    return ManifestRecord.model_validate(fields)


@dataclass(frozen=True)
class Case:
    lastmod: datetime | None = T - 2 * DAY
    stored: dict[str, Any] | None = field(default_factory=dict)  # None = not stored
    now: datetime = T + HOUR
    full: bool = False
    expected: FetchReason | None = None


CASES = {
    "not-in-manifest": Case(stored=None, expected=FetchReason.NEW),
    "full-on-unchanged": Case(full=True, expected=FetchReason.FULL),
    "failed": Case(
        stored={"status": "failed", "fetched_at": None}, expected=FetchReason.FAILED
    ),
    "gone-lastmod-after-attempt": Case(
        lastmod=T + HOUR, stored={"status": "gone"}, expected=FetchReason.CHANGED
    ),
    "gone-lastmod-before-attempt": Case(lastmod=T - HOUR, stored={"status": "gone"}),
    "gone-no-lastmod": Case(lastmod=None, stored={"status": "gone"}),
    "never-fetched": Case(
        stored={"fetched_at": None}, expected=FetchReason.UNKNOWN_TIME
    ),
    "changed-one-second-later": Case(lastmod=T + SECOND, expected=FetchReason.CHANGED),
    "edited-at-fetch-time-day-later": Case(
        lastmod=T, now=T + DAY, expected=FetchReason.LOOKBACK
    ),
    "edited-hour-before-too-soon": Case(lastmod=T - HOUR, now=T + 23 * HOUR),
    "edited-hour-before-day-later": Case(
        lastmod=T - HOUR, now=T + DAY, expected=FetchReason.LOOKBACK
    ),
    "edited-exactly-day-before": Case(
        lastmod=T - DAY, now=T + DAY, expected=FetchReason.LOOKBACK
    ),
    "edited-just-over-day-before": Case(lastmod=T - DAY - SECOND, now=T + DAY),
    "lookback-already-done": Case(
        lastmod=T - HOUR, stored={"lookback_done": True}, now=T + DAY
    ),
    "no-lastmod-good-copy": Case(lastmod=None, now=T + 10 * DAY),
    "unchanged": Case(),
}


@pytest.mark.parametrize("case", CASES.values(), ids=CASES.keys())
def test_select_reason(case: Case) -> None:
    manifest = {} if case.stored is None else {"x": record(**case.stored)}

    selected = select_reviews_to_fetch(
        [entry(lastmod=case.lastmod)], manifest, now=case.now, full=case.full
    )

    reasons = [reason for _, reason in selected]
    assert reasons == ([] if case.expected is None else [case.expected])


def test_ordering_newest_first_none_last() -> None:
    entries = [
        entry("old", T - 3 * DAY),
        entry("none", None),
        entry("new", T),
        entry("b-tie", T - DAY),
        entry("a-tie", T - DAY),
    ]

    selected = select_reviews_to_fetch(entries, {}, now=T, full=False)

    assert [e.slug for e, _ in selected] == ["new", "a-tie", "b-tie", "old", "none"]


def test_find_delistings_marks_missing_and_skips_already_delisted() -> None:
    manifest = {
        "kept": record("kept"),
        "gone-from-sitemap": record("gone-from-sitemap"),
        "already-delisted": record("already-delisted", delisted_at=T),
    }

    plan = find_delistings([entry("kept")], manifest)

    assert plan == Delistings(
        delisted=frozenset({"gone-from-sitemap"}), relisted=frozenset()
    )


def test_find_delistings_relists_reappearing() -> None:
    manifest = {"back": record("back", delisted_at=T)}

    plan = find_delistings([entry("back")], manifest)

    assert plan == Delistings(delisted=frozenset(), relisted=frozenset({"back"}))


@pytest.mark.parametrize(
    ("delisted", "manifest_size", "exceeds"),
    [
        (3, 40, False),
        (25, 40, False),
        (26, 40, True),
        (26, 1000, False),
        (470, 9400, False),
        (471, 9400, True),
        (0, 0, False),
    ],
    ids=[
        "few-from-small-manifest",
        "at-the-floor",
        "over-floor-and-percent",
        "over-floor-under-percent",
        "exactly-5-percent-of-full-site",
        "over-5-percent-of-full-site",
        "empty-manifest",
    ],
)
def test_delisting_limit(delisted: int, manifest_size: int, exceeds: bool) -> None:
    plan = Delistings(
        delisted=frozenset(f"s{i}" for i in range(delisted)), relisted=frozenset()
    )

    assert delisting_exceeds_limit(plan, manifest_size) is exceeds
