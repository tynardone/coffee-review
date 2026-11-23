"""The reference-store contracts: every test runs against every implementation,
so the in-memory fakes behave exactly like the file stores."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
from typing import TYPE_CHECKING

import polars as pl
import pytest

from coffee_review.models.reference import CpiResponse, FxResponse
from coffee_review.storage.reference_store import (
    FileReferenceRawStore,
    ParquetReferenceStore,
    ReferenceLayerMissingError,
)
from tests.fakes import InMemoryReferenceRawStore, InMemoryReferenceStore
from tests.models.test_reference_contract import frame

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from coffee_review.models.reference import ReferenceTable
    from coffee_review.storage.reference_store import (
        ReferenceRawStore,
        ReferenceStore,
    )

OXR = "openexchangerates"


@pytest.fixture(params=["files", "in_memory"])
def raw(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[ReferenceRawStore]:
    store: ReferenceRawStore = (
        FileReferenceRawStore(tmp_path / "raw")
        if request.param == "files"
        else InMemoryReferenceRawStore()
    )
    yield store
    store.close()


@pytest.fixture(params=["parquet", "in_memory"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[ReferenceStore]:
    tidy: ReferenceStore = (
        ParquetReferenceStore(tmp_path / "reference")
        if request.param == "parquet"
        else InMemoryReferenceStore()
    )
    yield tidy
    tidy.close()


def cpi_response(**changes: object) -> CpiResponse:
    fields: dict[str, object] = {
        "source": "bls",
        "series": "CUUR0000SA0",
        "fetched_at": datetime(2026, 11, 22, 12, 30, 5, tzinfo=UTC),
        "start_year": 2017,
        "end_year": 2026,
    }
    return CpiResponse.model_validate(fields | changes)


def test_fx_save_list_read(raw: ReferenceRawStore) -> None:
    response = FxResponse(source=OXR, day=date(2020, 1, 1))

    raw.save_fx(response, '{"a": 1}')

    assert raw.list_fx() == [response]
    assert raw.read_fx(response) == '{"a": 1}'


def test_fx_nothing_saved_lists_nothing(raw: ReferenceRawStore) -> None:
    assert raw.list_fx() == []
    assert raw.list_cpi() == []


def test_fx_save_again_replaces(raw: ReferenceRawStore) -> None:
    response = FxResponse(source=OXR, day=date(2020, 1, 1))

    raw.save_fx(response, "{")
    raw.save_fx(response, '{"fixed": true}')

    assert raw.list_fx() == [response]
    assert raw.read_fx(response) == '{"fixed": true}'


def test_fx_text_round_trips_unicode(raw: ReferenceRawStore) -> None:
    response = FxResponse(source=OXR, day=date(2020, 1, 1))

    raw.save_fx(response, '{"note": "Kč ¥ €"}')

    assert raw.read_fx(response) == '{"note": "Kč ¥ €"}'


def test_sources_listed_together(raw: ReferenceRawStore) -> None:
    a = FxResponse(source="a", day=date(2020, 1, 1))
    b = FxResponse(source="b", day=date(2020, 1, 1))

    raw.save_fx(a, "{}")
    raw.save_fx(b, "{}")

    assert sorted(raw.list_fx(), key=lambda r: r.source) == [a, b]


def test_cpi_save_list_read(raw: ReferenceRawStore) -> None:
    response = cpi_response()

    raw.save_cpi(response, '{"b": 2}')

    assert raw.list_cpi() == [response]
    assert raw.read_cpi(response) == '{"b": 2}'


def test_cpi_fetched_at_round_trips_as_utc(raw: ReferenceRawStore) -> None:
    eastern = timezone(timedelta(hours=-5))
    response = cpi_response(fetched_at=datetime(2026, 11, 22, 7, 30, 5, tzinfo=eastern))

    raw.save_cpi(response, "{}")

    listed = raw.list_cpi()[0]
    assert listed.fetched_at == datetime(2026, 11, 22, 12, 30, 5, tzinfo=UTC)
    assert listed.fetched_at.tzinfo == UTC


def test_cpi_two_requests_both_kept(raw: ReferenceRawStore) -> None:
    first = cpi_response()
    later = cpi_response(fetched_at=datetime(2026, 12, 20, 9, tzinfo=UTC))

    raw.save_cpi(first, "{}")
    raw.save_cpi(later, "{}")

    assert sorted(raw.list_cpi(), key=lambda r: r.fetched_at) == [first, later]


@pytest.mark.parametrize("table", ["fx", "cpi", "fx_gaps", "cpi_gaps"])
def test_tidy_write_then_read_each_table(
    store: ReferenceStore, table: ReferenceTable
) -> None:
    written = frame(table)

    store.write(table, written)

    read = store.read_table(table)
    assert read.equals(written)
    assert read.schema == written.schema


def test_tidy_rewrite_replaces(store: ReferenceStore) -> None:
    store.write("fx", frame("fx"))
    store.write("fx", frame("fx", currency="CAD"))

    assert store.read_table("fx")["currency"].to_list() == ["CAD"]


def test_tidy_read_before_write_raises(store: ReferenceStore) -> None:
    with pytest.raises(ReferenceLayerMissingError):
        store.read_table("fx")


def test_tidy_location_names_the_table(store: ReferenceStore) -> None:
    assert "fx" in store.location("fx")


def test_empty_table_round_trips(store: ReferenceStore) -> None:
    empty = frame("fx").clear()

    store.write("fx", empty)

    assert store.read_table("fx").schema == pl.Schema(empty.schema)
    assert store.read_table("fx").is_empty()
