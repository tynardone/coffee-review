"""The RawStore contract: every test here runs against every implementation.

The ``store`` fixture is parametrized, so each test runs twice, once against
the real gzip+SQLite store and once against the in-memory fake. That's what
makes the fake trustworthy as a stand-in in the pipeline tests: it is held to
exactly the same behavior.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from coffee_review.models.scrape import SitemapEntry
from coffee_review.storage.raw_store import GzipSqliteRawStore, PageNotFoundError
from tests.fakes import InMemoryRawStore

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from coffee_review.storage.raw_store import RawStore

T = datetime(2026, 11, 1, 12, tzinfo=UTC)
LATER = T + timedelta(days=3)


def entry(
    slug: str = "x", lastmod: datetime | None = T - timedelta(days=1)
) -> SitemapEntry:
    return SitemapEntry(
        slug=slug, url=f"https://www.coffeereview.com/review/{slug}/", lastmod=lastmod
    )


@pytest.fixture(params=["gzip_sqlite", "in_memory"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[RawStore]:
    raw_store: RawStore = (
        GzipSqliteRawStore(tmp_path / "raw")
        if request.param == "gzip_sqlite"
        else InMemoryRawStore()
    )
    yield raw_store
    raw_store.close()


def test_empty_manifest(store: RawStore) -> None:
    assert store.load_manifest() == {}


def test_save_then_read_page(store: RawStore) -> None:
    store.save_page(entry(), "<html>café</html>", T, lookback=False)

    assert store.read_page("x") == "<html>café</html>"


def test_save_records_manifest_row(store: RawStore) -> None:
    store.save_page(entry(), "<html/>", T, lookback=False)

    row = store.load_manifest()["x"]
    assert row.status == "ok"
    assert row.fetched_at == T
    assert row.last_attempt_at == T
    assert row.first_seen_at == T
    assert row.lastmod == T - timedelta(days=1)
    assert row.last_error is None
    assert row.delisted_at is None


def test_resave_keeps_first_seen_at(store: RawStore) -> None:
    store.save_page(entry(), "<html>v1</html>", T, lookback=False)
    store.save_page(entry(lastmod=LATER), "<html>v2</html>", LATER, lookback=False)

    row = store.load_manifest()["x"]
    assert row.first_seen_at == T
    assert row.fetched_at == LATER
    assert row.lastmod == LATER
    assert store.read_page("x") == "<html>v2</html>"


def test_lookback_flag_set_and_reset(store: RawStore) -> None:
    store.save_page(entry(), "<html/>", T, lookback=True)
    assert store.load_manifest()["x"].lookback_done is True

    store.save_page(entry(), "<html/>", LATER, lookback=False)
    assert store.load_manifest()["x"].lookback_done is False


def test_failure_keeps_previous_page_and_fetched_at(store: RawStore) -> None:
    store.save_page(entry(), "<html>good</html>", T, lookback=False)

    store.record_failure(entry(), "HTTP 500", LATER)

    row = store.load_manifest()["x"]
    assert row.status == "failed"
    assert row.last_error == "HTTP 500"
    assert row.fetched_at == T
    assert row.last_attempt_at == LATER
    assert store.read_page("x") == "<html>good</html>"


def test_failure_on_new_slug_has_no_fetched_at(store: RawStore) -> None:
    store.record_failure(entry(), "timeout", T)

    row = store.load_manifest()["x"]
    assert row.fetched_at is None
    assert row.first_seen_at == T
    assert row.status == "failed"


def test_record_gone_sets_status(store: RawStore) -> None:
    store.record_gone(entry(), "HTTP 404", T)

    row = store.load_manifest()["x"]
    assert row.status == "gone"
    assert row.last_error == "HTTP 404"


def test_mark_delisted_and_relisted(store: RawStore) -> None:
    store.save_page(entry("a"), "<html/>", T, lookback=False)
    store.save_page(entry("b"), "<html/>", T, lookback=False)

    store.mark_delisted(["a", "b"], LATER)
    assert store.load_manifest()["a"].delisted_at == LATER

    store.mark_relisted(["b"])
    manifest = store.load_manifest()
    assert manifest["a"].delisted_at == LATER
    assert manifest["b"].delisted_at is None


def test_read_missing_page_raises(store: RawStore) -> None:
    with pytest.raises(PageNotFoundError):
        store.read_page("never-saved")


@pytest.mark.parametrize("slug", ["../x", "a/b", ""], ids=["dotdot", "slash", "empty"])
def test_read_page_rejects_unsafe_slug(store: RawStore, slug: str) -> None:
    # The parse stage reads pages by slug; a slug that isn't one must never
    # become a path.
    with pytest.raises(ValueError, match="slug"):
        store.read_page(slug)
