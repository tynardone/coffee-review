"""File-specific behaviour of the reference stores: where things go on disk."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

from coffee_review.models.reference import CpiResponse, FxResponse
from coffee_review.storage.reference_store import (
    FileReferenceRawStore,
    ParquetReferenceStore,
)
from tests.models.test_reference_contract import frame

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_fx_file_path(tmp_path: Path) -> None:
    raw = FileReferenceRawStore(tmp_path)

    raw.save_fx(FxResponse(source="openexchangerates", day=date(2020, 3, 1)), "{}")

    assert (tmp_path / "fx" / "openexchangerates" / "2020-03-01.json").read_text(
        encoding="utf-8"
    ) == "{}"


def test_cpi_file_path(tmp_path: Path) -> None:
    raw = FileReferenceRawStore(tmp_path)
    response = CpiResponse(
        source="bls",
        series="CUUR0000SA0",
        fetched_at=datetime(2026, 11, 22, 12, 30, 5, tzinfo=UTC),
        start_year=1997,
        end_year=2006,
    )

    raw.save_cpi(response, "{}")

    expected = (
        tmp_path / "cpi" / "bls" / "CUUR0000SA0" / "20261122T123005Z_1997-2006.json"
    )
    assert expected.exists()


def test_stray_files_skipped_with_a_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    raw = FileReferenceRawStore(tmp_path)
    raw.save_fx(FxResponse(source="s", day=date(2020, 1, 1)), "{}")
    (tmp_path / "fx" / "s" / "notes.txt").write_text("hi", encoding="utf-8")
    (tmp_path / "fx" / "s" / "2020-13-01.json").write_text("{}", encoding="utf-8")
    cpi_dir = tmp_path / "cpi" / "bls" / "CUUR0000SA0"
    cpi_dir.mkdir(parents=True)
    (cpi_dir / "latest.json").write_text("{}", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        fx = raw.list_fx()
        cpi = raw.list_cpi()

    assert fx == [FxResponse(source="s", day=date(2020, 1, 1))]
    assert cpi == []
    assert "notes.txt" in caplog.text
    assert "2020-13-01.json" in caplog.text
    assert "latest.json" in caplog.text


def test_no_temp_files_left(tmp_path: Path) -> None:
    raw = FileReferenceRawStore(tmp_path)
    raw.save_fx(FxResponse(source="s", day=date(2020, 1, 1)), "{}")

    assert [p.name for p in (tmp_path / "fx" / "s").iterdir()] == ["2020-01-01.json"]


def test_tidy_file_paths(tmp_path: Path) -> None:
    store = ParquetReferenceStore(tmp_path)

    for table in ("fx", "cpi", "fx_gaps", "cpi_gaps"):
        store.write(table, frame(table))

    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "cpi.parquet",
        "cpi_gaps.parquet",
        "fx.parquet",
        "fx_gaps.parquet",
    ]
    assert store.location("fx") == str(tmp_path / "fx.parquet")
