"""The CleanStore contract: every test runs against every implementation,
so the in-memory fake behaves exactly like the Parquet store."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from coffee_review.models.clean import LOG_SCHEMA
from coffee_review.storage.clean_store import ParquetCleanStore
from tests.fakes import InMemoryCleanStore
from tests.models.test_clean_contract import ROASTED_ROW, frame

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    import polars as pl

    from coffee_review.storage.clean_store import CleanStore


@pytest.fixture(params=["parquet", "in_memory"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[CleanStore]:
    clean: CleanStore = (
        ParquetCleanStore(tmp_path / "clean")
        if request.param == "parquet"
        else InMemoryCleanStore()
    )
    yield clean
    clean.close()


def tables() -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    return frame("roasted"), frame("green"), frame("log")


def test_write_then_read_each_table(store: CleanStore) -> None:
    roasted, green, log = tables()

    store.write(roasted, green, log, "# Clean report")

    assert store.read_table("roasted").equals(roasted)
    assert store.read_table("roasted").schema == roasted.schema
    assert store.read_table("green").equals(green)
    assert store.read_table("log").equals(log)


def test_rewrite_replaces_previous_rows(store: CleanStore) -> None:
    roasted, green, log = tables()
    two = frame("roasted", ROASTED_ROW, ROASTED_ROW | {"slug": "b"})
    store.write(two, green, log, "")

    store.write(roasted, green, log, "")

    assert store.read_table("roasted")["slug"].to_list() == ["a"]


def test_previous_log_on_first_run_is_empty(store: CleanStore) -> None:
    previous = store.read_previous_log()

    assert previous.height == 0
    assert previous.schema == LOG_SCHEMA


def test_previous_log_is_the_last_write(store: CleanStore) -> None:
    roasted, green, log = tables()
    store.write(roasted, green, log, "")

    assert store.read_previous_log().equals(log)


def test_empty_tables_round_trip(store: CleanStore) -> None:
    roasted, _, log = tables()
    no_green = frame("green").clear()

    store.write(roasted, no_green, log.clear(), "")

    assert store.read_table("green").height == 0
    assert store.read_table("green").schema == no_green.schema


def test_report_location(store: CleanStore) -> None:
    assert store.report_location()
