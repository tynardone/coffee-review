"""The scrape stage: sitemap → rerun rules → fetch → raw store.

``run_scrape`` does the work with its client and store passed in, so tests can
hand it fakes. ``scrape`` is the composition root that builds the real ones.
This module never sees how pages are stored: it only holds a ``RawStore``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from coffee_review.clients.coffee_site import (
    CoffeeScraper,
    Failed,
    Fetched,
    Gone,
    Pushback,
)
from coffee_review.models.scrape import FetchReason, RunMeasurement, ScrapeSummary
from coffee_review.parsers.sitemap import (
    SitemapParseError,
    dedupe_entries,
    parse_review_sitemap,
    parse_sitemap_index,
)
from coffee_review.pipeline.fetch_rules import (
    DELISTING_LIMIT,
    DELISTING_MIN_COUNT,
    delisting_exceeds_limit,
    find_delistings,
    select_reviews_to_fetch,
)
from coffee_review.pipeline.progress import NoProgress
from coffee_review.storage.raw_store import ManifestSchemaError, open_raw_store

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from coffee_review.config import Settings
    from coffee_review.models.scrape import SitemapEntry
    from coffee_review.pipeline.progress import ScrapeProgress
    from coffee_review.storage.raw_store import RawStore

logger = logging.getLogger(__name__)

MAX_CONSECUTIVE_FAILURES = 5


def scrape(
    settings: Settings,
    *,
    full: bool,
    limit: int | None,
    now: datetime,
    allow_delisting: bool = False,
    progress: ScrapeProgress | None = None,
) -> ScrapeSummary:
    """Run a scrape with the real client and store."""

    async def main() -> ScrapeSummary:
        try:
            store = open_raw_store(settings)
        except ManifestSchemaError as exc:
            summary = _ended_early("error", str(exc))
            logger.error(summary.describe())
            return summary
        try:
            async with CoffeeScraper(settings.coffee_site) as client:
                return await run_scrape(
                    settings,
                    client=client,
                    store=store,
                    full=full,
                    limit=limit,
                    now=now,
                    allow_delisting=allow_delisting,
                    progress=progress,
                )
        finally:
            store.close()

    return asyncio.run(main())


async def run_scrape(
    settings: Settings,
    *,
    client: CoffeeScraper,
    store: RawStore,
    full: bool,
    limit: int | None,
    now: datetime,
    allow_delisting: bool = False,
    clock: Callable[[], float] = time.monotonic,
    progress: ScrapeProgress | None = None,
) -> ScrapeSummary:
    """Fetch new and changed reviews into ``store``; stop on any pushback.

    ``allow_delisting`` lets a run mark more reviews delisted than the guard
    normally allows, for when a large delisting is known to be real.

    Every outcome, early endings included, comes back measured (ADR 0005).
    """
    site = settings.coffee_site
    progress = progress or NoProgress()
    logger.info(
        "scrape starting: rate %.1f/s, concurrency %d",
        site.rate_per_sec,
        site.max_concurrency,
    )
    started = clock()
    requests_before = client.stats.requests
    response_before = client.stats.response_seconds

    try:
        summary = await _scrape_steps(
            settings,
            client=client,
            store=store,
            full=full,
            limit=limit,
            now=now,
            allow_delisting=allow_delisting,
            progress=progress,
        )
    except Exception as exc:
        # Something unexpected (disk full, database locked) broke the run. Log
        # the traceback, and still end with a measured summary and exit 1.
        # Pages saved before the error stay saved.
        logger.exception("scrape failed")
        summary = _ended_early("error", _describe_error(exc))
    finally:
        progress.finish()  # before the summary, so it prints below the bar

    requests = client.stats.requests - requests_before
    response_seconds = client.stats.response_seconds - response_before
    measured = summary.model_copy(
        update={
            "measurement": RunMeasurement(
                requests=requests,
                elapsed_seconds=clock() - started,
                mean_response_seconds=response_seconds / requests if requests else None,
                rate_per_sec=site.rate_per_sec,
                max_concurrency=site.max_concurrency,
            )
        }
    )
    log = {"finished": logger.info, "stopped": logger.warning, "error": logger.error}
    log[measured.outcome](measured.describe())
    return measured


async def _scrape_steps(
    settings: Settings,
    *,
    client: CoffeeScraper,
    store: RawStore,
    full: bool,
    limit: int | None,
    now: datetime,
    allow_delisting: bool,
    progress: ScrapeProgress,
) -> ScrapeSummary:
    """The scrape itself: sitemaps, delistings, selection, fetching."""
    listing = await _read_sitemaps(client, str(settings.coffee_site.sitemap_url))
    if isinstance(listing, ScrapeSummary):
        return listing
    entries = listing

    manifest = store.load_manifest()
    delistings = find_delistings(entries, manifest)
    if delisting_exceeds_limit(delistings, len(manifest)):
        too_many = (
            f"sitemap would delist {len(delistings.delisted)} of {len(manifest)} "
            f"reviews (more than {DELISTING_MIN_COUNT} and {DELISTING_LIMIT:.0%})"
        )
        if not allow_delisting:
            return _ended_early(
                "error",
                f"{too_many}; nothing marked. If that's real, rerun with "
                "--allow-delisting",
            )
        logger.warning("%s; marking them anyway (--allow-delisting)", too_many)
    store.mark_delisted(delistings.delisted, now)
    store.mark_relisted(delistings.relisted)

    selected = select_reviews_to_fetch(entries, manifest, now=now, full=full)
    planned = selected[:limit] if limit is not None else selected
    progress.start(len(planned))
    run = _FetchRun(store=store, now=now, queue=deque(planned), progress=progress)
    async with asyncio.TaskGroup() as workers:
        for _ in range(settings.coffee_site.max_concurrency):
            workers.create_task(run.work(client))

    return ScrapeSummary(
        outcome="stopped" if run.stopped_reason else "finished",
        fetched=dict(run.fetched),
        unchanged=len(entries) - len(selected),
        failed=run.failed,
        gone_slugs=run.gone_slugs,
        delisted=len(delistings.delisted),
        relisted=len(delistings.relisted),
        remaining=len(run.queue) + len(selected) - len(planned),
        stopped_reason=run.stopped_reason,
    )


async def _read_sitemaps(
    client: CoffeeScraper, index_url: str
) -> list[SitemapEntry] | ScrapeSummary:
    """Every review in every review sitemap, or a summary saying why not.

    Any failure here ends the run before a single page is fetched or marked:
    a partial listing would make listed reviews look delisted.
    """
    try:
        index = await _fetch_sitemap_text(client, index_url)
        if isinstance(index, ScrapeSummary):
            return index
        sitemap_urls = parse_sitemap_index(index)
        if not sitemap_urls:
            return _ended_early("error", f"no review sitemaps listed in {index_url}")
        entries: list[SitemapEntry] = []
        for url in sitemap_urls:
            text = await _fetch_sitemap_text(client, url)
            if isinstance(text, ScrapeSummary):
                return text
            entries.extend(parse_review_sitemap(text))
    except SitemapParseError as exc:
        return _ended_early("error", str(exc))
    return dedupe_entries(entries)


async def _fetch_sitemap_text(client: CoffeeScraper, url: str) -> str | ScrapeSummary:
    match await client.fetch_sitemap(url):
        case Fetched(text=text):
            return text
        case Pushback(reason=reason):
            return _ended_early("stopped", f"{reason} at {url}")
        case Failed(error=reason) | Gone(reason=reason):
            return _ended_early("error", f"sitemap {url}: {reason}")


def _describe_error(exc: BaseException) -> str:
    """``Type: message`` for the error that actually happened.

    A worker's exception arrives wrapped in the TaskGroup's ExceptionGroup.
    """
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return f"{type(exc).__name__}: {exc}"


def _ended_early(outcome: Literal["stopped", "error"], reason: str) -> ScrapeSummary:
    return ScrapeSummary(
        outcome=outcome,
        fetched={},
        unchanged=0,
        failed=0,
        gone_slugs=[],
        delisted=0,
        relisted=0,
        remaining=0,
        stopped_reason=reason,
    )


@dataclass
class _FetchRun:
    """Shared state for the workers of one run."""

    store: RawStore
    now: datetime
    queue: deque[tuple[SitemapEntry, FetchReason]]
    progress: ScrapeProgress
    fetched: Counter[FetchReason] = field(default_factory=Counter)
    failed: int = 0
    gone_slugs: list[str] = field(default_factory=list)
    consecutive_failures: int = 0
    stopped_reason: str | None = None
    # Set the moment the run stops: workers take no new reviews, and the client
    # sends no more retries for reviews already in progress.
    stop_signal: asyncio.Event = field(default_factory=asyncio.Event)

    async def work(self, client: CoffeeScraper) -> None:
        """Take reviews off the queue until it's empty or the run is stopped.

        After a stop, no new request is sent: not for a new review, and not as a
        retry. A request already on the wire finishes and its result is saved.
        """
        while self.queue and self.stopped_reason is None:
            entry, reason = self.queue.popleft()
            logger.debug("fetching %s (%s)", entry.slug, reason)
            result = await client.fetch_review_page(entry, stop=self.stop_signal)
            self._record(entry, reason, result)

    def _record(
        self,
        entry: SitemapEntry,
        reason: FetchReason,
        result: Fetched | Failed | Gone | Pushback,
    ) -> None:
        match result:
            case Fetched(text=html):
                self.store.save_page(
                    entry, html, self.now, lookback=reason is FetchReason.LOOKBACK
                )
                self.fetched[reason] += 1
                self.consecutive_failures = 0
                self.progress.advance("saved")
            case Failed(error=error):
                logger.warning("failed %s: %s", entry.url, error)
                self.store.record_failure(entry, error, self.now)
                self.failed += 1
                self.consecutive_failures += 1
                self.progress.advance("failed")
                if self.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    self._stop(f"{MAX_CONSECUTIVE_FAILURES} failures in a row")
            case Gone(reason=gone_reason):
                logger.warning("gone %s: %s", entry.url, gone_reason)
                self.store.record_gone(entry, gone_reason, self.now)
                self.gone_slugs.append(entry.slug)
                self.progress.advance("gone")
            case Pushback(reason=pushback_reason, url=url):
                self._stop(f"{pushback_reason} at {url}")

    def _stop(self, reason: str) -> None:
        if self.stopped_reason is None:
            logger.warning("stopping: %s", reason)
            self.stopped_reason = reason
            self.stop_signal.set()
