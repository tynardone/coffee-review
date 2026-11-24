"""Test doubles: small working implementations used in place of real ones.

These are fakes, not mocks: they really behave like the thing they replace
(test_raw_store_contract.py holds InMemoryRawStore to the same tests as the
real store), so tests using them check behavior, not which calls were made.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
import polars as pl

from coffee_review.config import CoffeeSiteSettings
from coffee_review.models.clean import LOG_SCHEMA
from coffee_review.models.common import require_slug
from coffee_review.models.scrape import ManifestRecord
from coffee_review.storage.clean_store import CleanLayerMissingError
from coffee_review.storage.parsed_store import ParsedLayerMissingError, reviews_frame
from coffee_review.storage.raw_store import PageNotFoundError

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Sequence
    from datetime import datetime

    from coffee_review.models.clean import CleanTable
    from coffee_review.models.parsed import Dataset, ParsedReview, ParseIssue
    from coffee_review.models.scrape import SitemapEntry


class InMemoryRawStore:
    """A RawStore that keeps everything in dicts."""

    def __init__(self) -> None:
        self.pages: dict[str, str] = {}
        self.records: dict[str, ManifestRecord] = {}

    def load_manifest(self) -> dict[str, ManifestRecord]:
        return dict(self.records)

    def save_page(
        self, entry: SitemapEntry, html: str, fetched_at: datetime, *, lookback: bool
    ) -> None:
        self.pages[entry.slug] = html
        previous = self.records.get(entry.slug)
        self.records[entry.slug] = ManifestRecord(
            slug=entry.slug,
            url=entry.url,
            lastmod=entry.lastmod,
            fetched_at=fetched_at,
            last_attempt_at=fetched_at,
            status="ok",
            last_error=None,
            lookback_done=lookback,
            first_seen_at=previous.first_seen_at if previous else fetched_at,
            delisted_at=previous.delisted_at if previous else None,
        )

    def record_failure(
        self, entry: SitemapEntry, error: str, attempted_at: datetime
    ) -> None:
        self._record_unsuccessful(entry, "failed", error, attempted_at)

    def record_gone(
        self, entry: SitemapEntry, reason: str, attempted_at: datetime
    ) -> None:
        self._record_unsuccessful(entry, "gone", reason, attempted_at)

    def _record_unsuccessful(
        self,
        entry: SitemapEntry,
        status: str,
        error: str,
        attempted_at: datetime,
    ) -> None:
        previous = self.records.get(entry.slug)
        if previous is None:
            self.records[entry.slug] = ManifestRecord.model_validate(
                {
                    "slug": entry.slug,
                    "url": entry.url,
                    "lastmod": entry.lastmod,
                    "fetched_at": None,
                    "last_attempt_at": attempted_at,
                    "status": status,
                    "last_error": error,
                    "lookback_done": False,
                    "first_seen_at": attempted_at,
                    "delisted_at": None,
                }
            )
            return
        self.records[entry.slug] = previous.model_copy(
            update={
                "url": entry.url,
                "last_attempt_at": attempted_at,
                "status": status,
                "last_error": error,
            }
        )

    def mark_delisted(self, slugs: Collection[str], at: datetime) -> None:
        for slug in slugs:
            if slug in self.records:
                self.records[slug] = self.records[slug].model_copy(
                    update={"delisted_at": at}
                )

    def mark_relisted(self, slugs: Collection[str]) -> None:
        for slug in slugs:
            if slug in self.records:
                self.records[slug] = self.records[slug].model_copy(
                    update={"delisted_at": None}
                )

    def read_page(self, slug: str) -> str:
        try:
            return self.pages[require_slug(slug)]
        except KeyError:
            raise PageNotFoundError(slug) from None

    def close(self) -> None:
        pass


def unthrottled_site_settings(**overrides: Any) -> CoffeeSiteSettings:
    """Site settings with the rate limit effectively off, for tests only.

    Real settings refuse more than 10 requests/second (ADR 0005). Tests talk to
    a fake site and shouldn't wait, so they skip that validation on purpose:
    ``model_copy`` sets fields without validating them.
    """
    return CoffeeSiteSettings().model_copy(update={"rate_per_sec": 1000, **overrides})


type Reply = httpx.Response | Callable[[], httpx.Response]


class FakeSite:
    """A fake web site: each path answers with a fixed response or a callable.

    Use a callable when a page should answer differently over time (e.g. a 429
    on the first run, then 200). ``requested`` lists every path asked for, in
    order, so tests can check what a run did or didn't fetch.
    """

    def __init__(self, routes: dict[str, Reply]) -> None:
        self.routes = routes
        self.requested: list[str] = []
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requested.append(path)
        reply = self.routes.get(path)
        if reply is None:
            return httpx.Response(404)
        response = reply() if callable(reply) else reply
        # A fresh copy each time, so the same route can be served repeatedly.
        return httpx.Response(
            response.status_code, headers=response.headers, content=response.content
        )


class InMemoryParsedStore:
    """A ParsedStore that keeps the last write in memory."""

    def __init__(self) -> None:
        self.records: list[ParsedReview] = []
        self.issues: list[ParseIssue] = []
        self.report_markdown = ""
        self.written = False

    def write(
        self,
        records: Sequence[ParsedReview],
        issues: Sequence[ParseIssue],
        report_markdown: str,
    ) -> None:
        self.records = list(records)
        self.issues = list(issues)
        self.report_markdown = report_markdown
        self.written = True

    def read_reviews(self, dataset: Dataset) -> list[ParsedReview]:
        mine = [r for r in self.records if r.dataset == dataset]
        return sorted(mine, key=lambda r: r.slug)

    def read_table(self, dataset: Dataset) -> pl.DataFrame:
        if not self.written:
            msg = "no parsed data in memory: run parse first"
            raise ParsedLayerMissingError(msg)
        return reviews_frame(self.records, dataset)

    def read_issues(self) -> list[ParseIssue]:
        return sorted(self.issues, key=lambda i: (i.slug, i.kind, i.detail))

    def report_location(self) -> str:
        return "in memory"

    def close(self) -> None:
        pass


class InMemoryCleanStore:
    """A CleanStore that keeps the last write in memory."""

    def __init__(self) -> None:
        self.tables: dict[str, pl.DataFrame] = {}
        self.report_markdown = ""
        self.writes = 0

    def write(
        self,
        roasted: pl.DataFrame,
        green: pl.DataFrame,
        log: pl.DataFrame,
        report_markdown: str,
    ) -> None:
        self.tables = {"roasted": roasted, "green": green, "log": log}
        self.report_markdown = report_markdown
        self.writes += 1

    def read_table(self, table: CleanTable) -> pl.DataFrame:
        if table not in self.tables:
            msg = "no clean data in memory: run clean first"
            raise CleanLayerMissingError(msg)
        return self.tables[table]

    def read_previous_log(self) -> pl.DataFrame:
        return self.tables.get("log", pl.DataFrame(schema=LOG_SCHEMA))

    def report_location(self) -> str:
        return "in memory"

    def close(self) -> None:
        pass
