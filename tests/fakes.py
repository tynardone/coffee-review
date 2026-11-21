"""Test doubles: small working implementations used in place of real ones.

These are fakes, not mocks: they really behave like the thing they replace
(test_raw_store_contract.py holds InMemoryRawStore to the same tests as the
real store), so tests using them check behavior, not which calls were made.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from coffee_review.models import ManifestRecord
from coffee_review.storage.raw_store import PageNotFoundError

if TYPE_CHECKING:
    from collections.abc import Collection
    from datetime import datetime

    from coffee_review.models import SitemapEntry


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
            return self.pages[slug]
        except KeyError:
            raise PageNotFoundError(slug) from None

    def close(self) -> None:
        pass
