"""Tests for the Parquet clean store's files (beyond the shared contract)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl
import pytest

from coffee_review.config import Settings
from coffee_review.storage.clean_store import (
    CleanLayerMissingError,
    ParquetCleanStore,
    open_clean_store,
)
from tests.models.test_clean_contract import frame

if TYPE_CHECKING:
    from pathlib import Path


def write_once(root: Path) -> ParquetCleanStore:
    store = ParquetCleanStore(root)
    store.write(frame("roasted"), frame("green"), frame("log"), "# Clean report\n")
    return store


def test_files_written(tmp_path: Path) -> None:
    write_once(tmp_path)

    for path in (
        "roasted/reviews.parquet",
        "green/reviews.parquet",
        "cleaning_log.parquet",
        "report.md",
    ):
        assert (tmp_path / path).exists(), path
    assert (tmp_path / "report.md").read_text(encoding="utf-8") == "# Clean report\n"


def test_report_location_is_the_file(tmp_path: Path) -> None:
    assert write_once(tmp_path).report_location() == str(tmp_path / "report.md")


def test_read_table_before_any_clean_raises(tmp_path: Path) -> None:
    with pytest.raises(CleanLayerMissingError, match="run clean first"):
        ParquetCleanStore(tmp_path).read_table("roasted")


def test_failed_write_keeps_previous_tables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = write_once(tmp_path)

    def disk_full(self: pl.DataFrame, file: object, **kwargs: object) -> None:
        raise OSError("No space left on device")

    monkeypatch.setattr(pl.DataFrame, "write_parquet", disk_full)
    with pytest.raises(OSError, match="No space"):
        store.write(frame("roasted").clear(), frame("green"), frame("log"), "")
    monkeypatch.undo()

    assert store.read_table("roasted")["slug"].to_list() == ["a"]
    assert not list(tmp_path.rglob("*.tmp"))


def test_open_clean_store_uses_data_dir(tmp_path: Path) -> None:
    store = open_clean_store(Settings(_env_file=None, data_dir=tmp_path))
    store.write(frame("roasted"), frame("green"), frame("log"), "")

    assert (tmp_path / "clean" / "report.md").exists()
