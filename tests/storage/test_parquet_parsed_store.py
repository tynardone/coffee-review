"""Tests specific to ParquetParsedStore: files, schemas, atomic writes."""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl
import pytest

from coffee_review.config import Settings
from coffee_review.models.parsed import GreenReview, RoastedReview
from coffee_review.storage.parsed_store import (
    ParquetParsedStore,
    ParsedStore,
    open_parsed_store,
)
from tests.fakes import InMemoryParsedStore
from tests.storage.test_parsed_store_contract import green, roasted

if TYPE_CHECKING:
    from pathlib import Path

# mypy checks these: both classes must satisfy the ParsedStore Protocol.
_real: type[ParsedStore] = ParquetParsedStore
_fake: type[ParsedStore] = InMemoryParsedStore

TEXT = pl.String()


def test_schema_matches_spec(tmp_path: Path) -> None:
    store = ParquetParsedStore(tmp_path)
    store.write([roasted("a")], [], "")

    roasted_schema = pl.read_parquet_schema(tmp_path / "roasted" / "reviews.parquet")
    green_schema = pl.read_parquet_schema(tmp_path / "green" / "reviews.parquet")

    assert list(roasted_schema.items()) == [
        ("slug", TEXT), ("url", TEXT), ("delisted", pl.Boolean()),
        ("sold_green", pl.Boolean()),
        *((column, TEXT) for column in RoastedReview.columns),
        ("extra_count", pl.Int32()),
    ]  # fmt: skip
    assert list(green_schema.items()) == [
        ("slug", TEXT), ("url", TEXT), ("delisted", pl.Boolean()),
        *((column, TEXT) for column in GreenReview.columns),
        ("extra_count", pl.Int32()),
    ]  # fmt: skip
    for dataset in ("roasted", "green"):
        extra_schema = pl.read_parquet_schema(
            tmp_path / dataset / "extra_fields.parquet"
        )
        assert dict(extra_schema) == {"slug": TEXT, "label": TEXT, "value": TEXT}
    assert dict(pl.read_parquet_schema(tmp_path / "parse_issues.parquet")) == {
        "slug": TEXT, "kind": TEXT, "detail": TEXT,
    }  # fmt: skip


def test_rows_sorted_by_slug(tmp_path: Path) -> None:
    ParquetParsedStore(tmp_path).write([roasted("b"), roasted("a")], [], "")

    frame = pl.read_parquet(tmp_path / "roasted" / "reviews.parquet")
    assert frame["slug"].to_list() == ["a", "b"]


def test_extra_count_column(tmp_path: Path) -> None:
    ParquetParsedStore(tmp_path).write(
        [roasted("a", extra={"x": "1", "y": "2"}), roasted("b")], [], ""
    )

    frame = pl.read_parquet(tmp_path / "roasted" / "reviews.parquet")
    assert frame["extra_count"].to_list() == [2, 0]


def test_empty_dataset_written_with_schema(tmp_path: Path) -> None:
    ParquetParsedStore(tmp_path).write([roasted("a")], [], "")

    frame = pl.read_parquet(tmp_path / "green" / "reviews.parquet")
    assert frame.height == 0
    assert "lot_id" in frame.columns


def test_report_markdown_written(tmp_path: Path) -> None:
    store = ParquetParsedStore(tmp_path)
    store.write([], [], "# Parse report\n")

    assert (tmp_path / "report.md").read_text(encoding="utf-8") == "# Parse report\n"
    assert store.report_location() == str(tmp_path / "report.md")


def test_failed_write_leaves_no_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ParquetParsedStore(tmp_path)
    store.write([green("old")], [], "")

    def disk_full(self: pl.DataFrame, file: object, **kwargs: object) -> None:
        raise OSError("No space left on device")

    monkeypatch.setattr(pl.DataFrame, "write_parquet", disk_full)
    with pytest.raises(OSError, match="No space"):
        store.write([green("new")], [], "")
    monkeypatch.undo()

    files = sorted(p.name for p in (tmp_path / "green").iterdir())
    assert files == ["extra_fields.parquet", "reviews.parquet"]
    assert [r.slug for r in store.read_reviews("green")] == ["old"]


def test_open_parsed_store_uses_data_dir(tmp_path: Path) -> None:
    store = open_parsed_store(Settings(_env_file=None, data_dir=tmp_path))
    store.write([], [], "")

    assert (tmp_path / "parsed" / "report.md").exists()
