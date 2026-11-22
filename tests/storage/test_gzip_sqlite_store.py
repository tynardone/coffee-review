"""Tests specific to GzipSqliteRawStore: files, SQLite, crash safety.

Behavior every store shares is in test_raw_store_contract.py.
"""

from __future__ import annotations

import gzip
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from coffee_review.config import Settings
from coffee_review.models.scrape import SitemapEntry
from coffee_review.storage.raw_store import (
    GzipSqliteRawStore,
    ManifestSchemaError,
    RawStore,
    open_raw_store,
)
from tests.fakes import InMemoryRawStore

# mypy checks these assignments: both classes must satisfy the RawStore Protocol.
_real: type[RawStore] = GzipSqliteRawStore
_fake: type[RawStore] = InMemoryRawStore

T = datetime(2026, 11, 1, 12, tzinfo=UTC)
ENTRY = SitemapEntry(slug="x", url="https://www.coffeereview.com/review/x/", lastmod=T)


def test_page_file_is_gzipped_html(tmp_path: Path) -> None:
    store = GzipSqliteRawStore(tmp_path)
    store.save_page(ENTRY, "<html>café</html>", T, lookback=False)
    store.close()

    data = (tmp_path / "reviews" / "x.html.gz").read_bytes()
    assert gzip.decompress(data).decode("utf-8") == "<html>café</html>"


def test_manifest_survives_reopen(tmp_path: Path) -> None:
    store = GzipSqliteRawStore(tmp_path)
    store.save_page(ENTRY, "<html/>", T, lookback=False)
    store.close()

    reopened = GzipSqliteRawStore(tmp_path)
    try:
        assert reopened.load_manifest()["x"].fetched_at == T
    finally:
        reopened.close()


def test_failed_write_leaves_previous_page_and_no_temp_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = GzipSqliteRawStore(tmp_path)
    store.save_page(ENTRY, "<html>old</html>", T, lookback=False)

    def crash(self: Path, target: object) -> Path:
        raise OSError("disk full")

    monkeypatch.setattr(Path, "replace", crash)
    with pytest.raises(OSError, match="disk full"):
        store.save_page(
            ENTRY, "<html>new</html>", T + timedelta(days=1), lookback=False
        )
    monkeypatch.undo()

    assert store.read_page("x") == "<html>old</html>"
    assert store.load_manifest()["x"].fetched_at == T
    assert [p.name for p in (tmp_path / "reviews").iterdir()] == ["x.html.gz"]
    store.close()


def test_unknown_schema_version_raises(tmp_path: Path) -> None:
    GzipSqliteRawStore(tmp_path).close()
    with sqlite3.connect(tmp_path / "manifest.sqlite") as conn:
        conn.execute("PRAGMA user_version = 99")
    conn.close()

    with pytest.raises(ManifestSchemaError):
        GzipSqliteRawStore(tmp_path)


def test_manifest_readable_while_open(tmp_path: Path) -> None:
    store = GzipSqliteRawStore(tmp_path)
    store.save_page(ENTRY, "<html/>", T, lookback=False)

    reader = sqlite3.connect(tmp_path / "manifest.sqlite")
    try:
        assert reader.execute("SELECT count(*) FROM reviews").fetchone() == (1,)
    finally:
        reader.close()
        store.close()


def test_open_raw_store_uses_data_dir(tmp_path: Path) -> None:
    store = open_raw_store(Settings(_env_file=None, data_dir=tmp_path))
    store.save_page(ENTRY, "<html/>", T, lookback=False)
    store.close()

    assert (tmp_path / "raw" / "reviews" / "x.html.gz").exists()
    assert (tmp_path / "raw" / "manifest.sqlite").exists()


def test_failed_temp_write_leaves_no_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The disk filling up (or Ctrl-C) during the write itself, before the rename.
    store = GzipSqliteRawStore(tmp_path)
    store.save_page(ENTRY, "<html>old</html>", T, lookback=False)

    def disk_full(self: Path, data: bytes) -> int:
        raise OSError("No space left on device")

    monkeypatch.setattr(Path, "write_bytes", disk_full)
    with pytest.raises(OSError, match="No space"):
        store.save_page(ENTRY, "<html>new</html>", T, lookback=False)
    monkeypatch.undo()

    assert [p.name for p in (tmp_path / "reviews").iterdir()] == ["x.html.gz"]
    assert store.read_page("x") == "<html>old</html>"
    store.close()
