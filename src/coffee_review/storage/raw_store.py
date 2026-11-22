"""The raw data layer: saved review pages and the manifest that tracks them.

Everything outside ``storage/`` talks to the ``RawStore`` Protocol and never
sees how pages are stored. ``GzipSqliteRawStore`` is the only code that knows
about gzip files, SQLite or paths; ``open_raw_store`` is the one place that
picks it. Swapping storage means a new class and a one-line change there.
"""

from __future__ import annotations

import gzip
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from coffee_review.models import ManifestRecord

if TYPE_CHECKING:
    from collections.abc import Collection
    from datetime import datetime

    from coffee_review.config import Settings
    from coffee_review.models import SitemapEntry


class PageNotFoundError(KeyError):
    """Raised when reading a page that was never saved."""


class ManifestSchemaError(Exception):
    """Raised when the manifest was written by an unknown schema version."""


class RawStore(Protocol):
    """Saved review pages plus a manifest row per review."""

    def load_manifest(self) -> dict[str, ManifestRecord]:
        """Every manifest row, keyed by slug."""
        ...

    def save_page(
        self, entry: SitemapEntry, html: str, fetched_at: datetime, *, lookback: bool
    ) -> None:
        """Save a successfully fetched page and mark its row ``ok``."""
        ...

    def record_failure(
        self, entry: SitemapEntry, error: str, attempted_at: datetime
    ) -> None:
        """Mark a temporary failure. Any previously saved page is kept."""
        ...

    def record_gone(
        self, entry: SitemapEntry, reason: str, attempted_at: datetime
    ) -> None:
        """Mark a page as gone (404, redirected away). Any saved page is kept."""
        ...

    def mark_delisted(self, slugs: Collection[str], at: datetime) -> None:
        """Record that these reviews are no longer listed in the sitemap."""
        ...

    def mark_relisted(self, slugs: Collection[str]) -> None:
        """Record that these reviews are listed in the sitemap again."""
        ...

    def read_page(self, slug: str) -> str:
        """The saved HTML for ``slug``. Raises PageNotFoundError."""
        ...

    def close(self) -> None:
        """Release any open resources."""
        ...


def open_raw_store(settings: Settings) -> RawStore:
    """The raw store the pipeline uses."""
    return GzipSqliteRawStore(settings.data_dir / "raw")


SCHEMA_VERSION = 1

_CREATE_REVIEWS = """
CREATE TABLE reviews (
    slug            TEXT PRIMARY KEY,
    url             TEXT NOT NULL,
    lastmod         TEXT,
    fetched_at      TEXT,
    last_attempt_at TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('ok', 'failed', 'gone')),
    last_error      TEXT,
    lookback_done   INTEGER NOT NULL,
    first_seen_at   TEXT NOT NULL,
    delisted_at     TEXT
)
"""

# On a repeat save, first_seen_at and delisted_at are left as they were.
_UPSERT_OK = """
INSERT INTO reviews (slug, url, lastmod, fetched_at, last_attempt_at, status,
                     last_error, lookback_done, first_seen_at, delisted_at)
VALUES (:slug, :url, :lastmod, :at, :at, 'ok', NULL, :lookback, :at, NULL)
ON CONFLICT (slug) DO UPDATE SET
    url = excluded.url,
    lastmod = excluded.lastmod,
    fetched_at = excluded.fetched_at,
    last_attempt_at = excluded.last_attempt_at,
    status = 'ok',
    last_error = NULL,
    lookback_done = excluded.lookback_done
"""

# On a repeat failure, the saved copy's fetched_at and lastmod are kept.
_UPSERT_FAILED = """
INSERT INTO reviews (slug, url, lastmod, fetched_at, last_attempt_at, status,
                     last_error, lookback_done, first_seen_at, delisted_at)
VALUES (:slug, :url, :lastmod, NULL, :at, :status, :error, 0, :at, NULL)
ON CONFLICT (slug) DO UPDATE SET
    url = excluded.url,
    last_attempt_at = excluded.last_attempt_at,
    status = excluded.status,
    last_error = excluded.last_error
"""


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


class GzipSqliteRawStore:
    """Pages as ``reviews/<slug>.html.gz``; the manifest in ``manifest.sqlite``."""

    def __init__(self, root: Path) -> None:
        self._pages = root / "reviews"
        self._pages.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(root / "manifest.sqlite")
        self._db.row_factory = sqlite3.Row
        # WAL lets another process read the manifest while a scrape writes to it.
        self._db.execute("PRAGMA journal_mode = WAL")
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        (version,) = self._db.execute("PRAGMA user_version").fetchone()
        if version == SCHEMA_VERSION:
            return
        if version != 0:
            self._db.close()
            raise ManifestSchemaError(
                f"manifest schema version {version}; this code reads version "
                f"{SCHEMA_VERSION}"
            )
        with self._db:
            self._db.execute(_CREATE_REVIEWS)
            self._db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def _page_path(self, slug: str) -> Path:
        return self._pages / f"{slug}.html.gz"

    def load_manifest(self) -> dict[str, ManifestRecord]:
        rows = self._db.execute("SELECT * FROM reviews").fetchall()
        return {row["slug"]: ManifestRecord.model_validate(dict(row)) for row in rows}

    def save_page(
        self, entry: SitemapEntry, html: str, fetched_at: datetime, *, lookback: bool
    ) -> None:
        self._write_page_atomically(entry.slug, gzip.compress(html.encode("utf-8")))
        self._execute(
            _UPSERT_OK,
            slug=entry.slug,
            url=entry.url,
            lastmod=_iso(entry.lastmod),
            at=_iso(fetched_at),
            lookback=int(lookback),
        )

    def _write_page_atomically(self, slug: str, data: bytes) -> None:
        """Write to a temp file, then rename over the old page in one step.

        If this program crashes or is interrupted, the page is either the old
        one or the new one, never half of each, and no temp file is left
        behind. (A power cut could still lose the latest write; that would
        need an fsync, which this doesn't do.)
        """
        handle, name = tempfile.mkstemp(dir=self._pages, suffix=".tmp")
        os.close(handle)  # reopened below; mkstemp only reserves a unique name
        tmp_path = Path(name)
        try:
            tmp_path.write_bytes(data)
            tmp_path.replace(self._page_path(slug))
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise

    def record_failure(
        self, entry: SitemapEntry, error: str, attempted_at: datetime
    ) -> None:
        self._record_unsuccessful(entry, "failed", error, attempted_at)

    def record_gone(
        self, entry: SitemapEntry, reason: str, attempted_at: datetime
    ) -> None:
        self._record_unsuccessful(entry, "gone", reason, attempted_at)

    def _record_unsuccessful(
        self, entry: SitemapEntry, status: str, error: str, attempted_at: datetime
    ) -> None:
        self._execute(
            _UPSERT_FAILED,
            slug=entry.slug,
            url=entry.url,
            lastmod=_iso(entry.lastmod),
            at=_iso(attempted_at),
            status=status,
            error=error,
        )

    def mark_delisted(self, slugs: Collection[str], at: datetime) -> None:
        with self._db:
            self._db.executemany(
                "UPDATE reviews SET delisted_at = ? WHERE slug = ?",
                [(_iso(at), slug) for slug in slugs],
            )

    def mark_relisted(self, slugs: Collection[str]) -> None:
        with self._db:
            self._db.executemany(
                "UPDATE reviews SET delisted_at = NULL WHERE slug = ?",
                [(slug,) for slug in slugs],
            )

    def read_page(self, slug: str) -> str:
        try:
            data = self._page_path(slug).read_bytes()
        except FileNotFoundError:
            raise PageNotFoundError(slug) from None
        return gzip.decompress(data).decode("utf-8")

    def close(self) -> None:
        self._db.close()

    def _execute(self, sql: str, **params: Any) -> None:
        with self._db:  # commits on success, rolls back on error
            self._db.execute(sql, params)
