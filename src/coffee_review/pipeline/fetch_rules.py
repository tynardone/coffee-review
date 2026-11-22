"""The scraper's rerun rules: which reviews to fetch, and which left the sitemap.

Pure functions over models: no network, no storage. This is where the
incremental logic lives, so it's tested on its own with plain values.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, NamedTuple

from coffee_review.models import FetchReason, ManifestRecord, SitemapEntry

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

# A page edited this close before we downloaded it may have come from a stale
# cache, so it's fetched once more after the same amount of time has passed.
LOOKBACK = timedelta(hours=24)

# Marking more than this share of the manifest delisted in one run is treated as
# a broken sitemap, not real delistings...
DELISTING_LIMIT = 0.05
# ...but only once it's more than this many reviews, so a small manifest (40
# rows after a first --limit run) isn't blocked by a handful of real ones.
DELISTING_MIN_COUNT = 25


class Delistings(NamedTuple):
    delisted: frozenset[str]
    relisted: frozenset[str]


def fetch_reason(
    entry: SitemapEntry,
    stored: ManifestRecord | None,
    *,
    now: datetime,
    full: bool,
) -> FetchReason | None:
    """Why ``entry`` should be fetched this run, or None if it shouldn't be.

    Rules are checked in order; the first match wins.
    """
    lastmod = entry.lastmod
    if full:
        return FetchReason.FULL
    if stored is None:
        return FetchReason.NEW
    if stored.status == "failed":
        return FetchReason.FAILED
    if stored.status == "gone":
        if lastmod is not None and lastmod > stored.last_attempt_at:
            return FetchReason.CHANGED
        return None
    fetched_at = stored.fetched_at
    if fetched_at is None:
        return FetchReason.UNKNOWN_TIME
    if lastmod is None:
        return None
    if lastmod > fetched_at:
        return FetchReason.CHANGED
    if (
        fetched_at - LOOKBACK <= lastmod
        and not stored.lookback_done
        and now >= fetched_at + LOOKBACK
    ):
        return FetchReason.LOOKBACK
    return None


def select_reviews_to_fetch(
    entries: Sequence[SitemapEntry],
    manifest: Mapping[str, ManifestRecord],
    *,
    now: datetime,
    full: bool,
) -> list[tuple[SitemapEntry, FetchReason]]:
    """The reviews to fetch and why, newest lastmod first (unknown lastmod last).

    Newest first means new reviews are saved first if a run stops early.
    """
    selected = [
        (entry, reason)
        for entry in entries
        if (reason := fetch_reason(entry, manifest.get(entry.slug), now=now, full=full))
        is not None
    ]
    return sorted(selected, key=lambda pair: _newest_first(pair[0]))


def _newest_first(entry: SitemapEntry) -> tuple[bool, float, str]:
    if entry.lastmod is None:
        return (True, 0.0, entry.slug)
    return (False, -entry.lastmod.timestamp(), entry.slug)


def find_delistings(
    entries: Sequence[SitemapEntry],
    manifest: Mapping[str, ManifestRecord],
) -> Delistings:
    """Which stored reviews left the sitemap, and which came back."""
    listed = {entry.slug for entry in entries}
    delisted = frozenset(
        slug
        for slug, stored in manifest.items()
        if slug not in listed and stored.delisted_at is None
    )
    relisted = frozenset(
        slug
        for slug, stored in manifest.items()
        if slug in listed and stored.delisted_at is not None
    )
    return Delistings(delisted=delisted, relisted=relisted)


def delisting_exceeds_limit(plan: Delistings, manifest_size: int) -> bool:
    """True if the plan looks like a broken sitemap rather than real delistings.

    That's more than DELISTING_MIN_COUNT reviews *and* more than
    DELISTING_LIMIT of the manifest at once.
    """
    count = len(plan.delisted)
    if manifest_size == 0 or count <= DELISTING_MIN_COUNT:
        return False
    return count / manifest_size > DELISTING_LIMIT
