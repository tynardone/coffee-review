"""The scraper's rerun rules: which reviews to fetch, and which left the sitemap.

Pure functions over models: no network, no storage. This is where the
incremental logic lives, so it's tested on its own with plain values.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, NamedTuple

from coffee_review.models.scrape import FetchReason, ManifestRecord, SitemapEntry

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
    """Reviews that left the sitemaps this run, and ones that came back."""

    delisted: frozenset[str]
    relisted: frozenset[str]


def fetch_reason(
    entry: SitemapEntry,
    stored: ManifestRecord | None,
    *,
    now: datetime,
    full: bool,
) -> FetchReason | None:
    """Decide why a review should be fetched this run, if at all.

    Rules are checked in order; the first match wins.

    Args:
        entry: The review, as the sitemap lists it.
        stored: Its manifest row, or None if it's never been seen.
        now: The run's start time (UTC).
        full: Whether this is a ``--full`` run (fetch everything).

    Returns:
        The reason, or None if it shouldn't be fetched.
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
    """Choose the reviews to fetch this run, and why.

    Newest lastmod first (unknown lastmod last), so new reviews are saved first
    if a run stops early.

    Args:
        entries: Every review the sitemaps list.
        manifest: What the raw store knows, by slug.
        now: The run's start time (UTC).
        full: Whether this is a ``--full`` run.

    Returns:
        ``(entry, reason)`` pairs, in fetch order.
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
    """Find stored reviews that left the sitemaps, and ones that came back.

    Args:
        entries: Every review the sitemaps list now.
        manifest: What the raw store knows, by slug.

    Returns:
        The slugs to mark delisted and relisted.
    """
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
    """Check whether a delisting looks like a broken sitemap.

    That's more than ``DELISTING_MIN_COUNT`` reviews *and* more than
    ``DELISTING_LIMIT`` of the manifest at once.

    Args:
        plan: The delistings found this run.
        manifest_size: How many reviews the manifest holds.

    Returns:
        True if the run shouldn't mark them without ``--allow-delisting``.
    """
    count = len(plan.delisted)
    if manifest_size == 0 or count <= DELISTING_MIN_COUNT:
        return False
    return count / manifest_size > DELISTING_LIMIT
