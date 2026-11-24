"""Tests for coffee_review.storage.economic_store."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta, timezone
from typing import TYPE_CHECKING

import pytest

from coffee_review.models.economic import CpiFile
from coffee_review.storage.economic_store import (
    EconomicLayerMissingError,
    EconomicStore,
)
from tests.models.test_economic_contract import frame

if TYPE_CHECKING:
    from pathlib import Path

    from coffee_review.models.economic import EconomicTable

SERIES = "CUUR0000SA0"
FILE = CpiFile(
    fetched_at=datetime(2026, 11, 22, 12, 30, 5, tzinfo=UTC),
    start_year=1997,
    end_year=2006,
)


def test_fx_save_list_read(tmp_path: Path) -> None:
    store = EconomicStore(tmp_path)

    store.save_fx(date(2020, 3, 1), '{"note": "Kč ¥"}')

    assert store.fx_days() == [date(2020, 3, 1)]
    assert store.read_fx(date(2020, 3, 1)) == '{"note": "Kč ¥"}'
    path = tmp_path / "raw" / "fx" / "openexchangerates" / "2020-03-01.json"
    assert path.exists()


def test_fx_save_again_replaces(tmp_path: Path) -> None:
    store = EconomicStore(tmp_path)

    store.save_fx(date(2020, 3, 1), "{")
    store.save_fx(date(2020, 3, 1), "{}")

    assert store.read_fx(date(2020, 3, 1)) == "{}"
    folder = tmp_path / "raw" / "fx" / "openexchangerates"
    assert [p.name for p in folder.iterdir()] == ["2020-03-01.json"]  # no temp files


def test_nothing_saved_lists_nothing(tmp_path: Path) -> None:
    store = EconomicStore(tmp_path)

    assert store.fx_days() == []
    assert store.cpi_files(SERIES) == []


def test_cpi_save_list_read(tmp_path: Path) -> None:
    store = EconomicStore(tmp_path)

    store.save_cpi(SERIES, FILE, "{}")

    assert store.cpi_files(SERIES) == [FILE]
    assert store.read_cpi(SERIES, FILE) == "{}"
    name = "20261122T123005Z_1997-2006.json"
    assert (tmp_path / "raw" / "cpi" / "bls" / SERIES / name).exists()


def test_cpi_fetched_at_named_in_utc(tmp_path: Path) -> None:
    store = EconomicStore(tmp_path)
    eastern = timezone(timedelta(hours=-5))
    local = FILE.model_copy(
        update={"fetched_at": datetime(2026, 11, 22, 7, 30, 5, tzinfo=eastern)}
    )

    store.save_cpi(SERIES, local, "{}")

    assert store.cpi_files(SERIES)[0].fetched_at == FILE.fetched_at


def test_cpi_files_oldest_first(tmp_path: Path) -> None:
    store = EconomicStore(tmp_path)
    later = FILE.model_copy(
        update={"fetched_at": datetime(2026, 12, 1, tzinfo=UTC), "start_year": 2026}
    )

    store.save_cpi(SERIES, later, "{}")
    store.save_cpi(SERIES, FILE, "{}")

    assert store.cpi_files(SERIES) == [FILE, later]


def test_stray_files_skipped_with_a_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = EconomicStore(tmp_path)
    store.save_fx(date(2020, 1, 1), "{}")
    fx_dir = tmp_path / "raw" / "fx" / "openexchangerates"
    (fx_dir / "notes.txt").write_text("hi", encoding="utf-8")
    (fx_dir / "2020-13-01.json").write_text("{}", encoding="utf-8")
    cpi_dir = tmp_path / "raw" / "cpi" / "bls" / SERIES
    cpi_dir.mkdir(parents=True)
    (cpi_dir / "latest.json").write_text("{}", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        days = store.fx_days()
        files = store.cpi_files(SERIES)

    assert days == [date(2020, 1, 1)]
    assert files == []
    for name in ("notes.txt", "2020-13-01.json", "latest.json"):
        assert name in caplog.text


@pytest.mark.parametrize("table", ["fx", "cpi"])
def test_table_write_then_read(tmp_path: Path, table: EconomicTable) -> None:
    store = EconomicStore(tmp_path)
    written = frame(table)

    store.write_table(table, written)

    assert store.read_table(table).equals(written)
    assert store.location(table) == str(tmp_path / "economic" / f"{table}.parquet")


def test_table_read_before_write_raises(tmp_path: Path) -> None:
    with pytest.raises(EconomicLayerMissingError, match="run fx or cpi first"):
        EconomicStore(tmp_path).read_table("fx")
