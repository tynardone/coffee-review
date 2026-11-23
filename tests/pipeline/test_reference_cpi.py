"""Tests for the cpi stage in coffee_review.pipeline.reference."""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime

import pytest

from coffee_review.clients.reference import SourceRefusedError
from coffee_review.models.reference import CpiResponse
from coffee_review.parsers.cpi_bls import parse_cpi_bls
from coffee_review.pipeline.reference import (
    CPI_SOURCES,
    CpiSource,
    ReferenceRunResult,
    run_cpi,
)
from tests.fakes import (
    FakeCpiClient,
    InMemoryReferenceRawStore,
    InMemoryReferenceStore,
    bls_body,
)

SERIES = "CUUR0000SA0"
START = date(1997, 1, 1)
NOW = datetime(2026, 11, 22, 12, tzinfo=UTC)
OCT_2025 = date(2025, 10, 1)


def bls_like(max_years: int = 10) -> CpiSource:
    return CpiSource(
        client=CPI_SOURCES["bls"].client, parse=parse_cpi_bls, max_years=max_years
    )


async def run(
    client: FakeCpiClient,
    raw: InMemoryReferenceRawStore,
    store: InMemoryReferenceStore,
    *,
    now: datetime = NOW,
    name: str = "bls",
) -> ReferenceRunResult:
    return await run_cpi(
        source_name=name,
        sources={"bls": bls_like(), "other": bls_like()},
        client=client,
        raw=raw,
        store=store,
        series=SERIES,
        start=START,
        now=now,
    )


async def test_first_run_three_requests_then_zero() -> None:
    client = FakeCpiClient.published_through(date(2026, 10, 1), missing={OCT_2025})
    raw, store = InMemoryReferenceRawStore(), InMemoryReferenceStore()

    first = await run(client, raw, store)
    again = await run(client, raw, store, now=NOW.replace(hour=13))

    assert client.calls == [
        (SERIES, 1997, 2006),
        (SERIES, 2007, 2016),
        (SERIES, 2017, 2026),
    ]
    assert (first.fetched, first.gaps) == (3, 1)
    assert (again.requests, again.fetched) == (0, 0)
    assert store.read_table("cpi_gaps")["month"].to_list() == [OCT_2025]
    cpi = store.read_table("cpi")
    assert cpi.height == 12 * 29 + 10 - 1  # 1997-01 to 2026-10, without 2025-10
    assert (again.first, again.last) == (date(1997, 1, 1), date(2026, 10, 1))


async def test_next_day_asks_again_only_if_a_month_is_missing() -> None:
    raw, store = InMemoryReferenceRawStore(), InMemoryReferenceStore()
    await run(FakeCpiClient.published_through(date(2026, 9, 1)), raw, store)

    client = FakeCpiClient.published_through(date(2026, 10, 1))
    later = await run(client, raw, store, now=datetime(2026, 11, 23, 8, tzinfo=UTC))

    assert client.calls == [(SERIES, 2026, 2026)]
    assert later.last == date(2026, 10, 1)


async def test_next_month_asks_only_this_year() -> None:
    raw, store = InMemoryReferenceRawStore(), InMemoryReferenceStore()
    await run(FakeCpiClient.published_through(date(2026, 10, 1)), raw, store)

    client = FakeCpiClient.published_through(date(2026, 11, 1))
    await run(client, raw, store, now=datetime(2026, 12, 20, 9, tzinfo=UTC))

    assert client.calls == [(SERIES, 2026, 2026)]


async def test_refused_daily_limit_stops_with_saved_kept() -> None:
    published = FakeCpiClient.published_through(date(2026, 10, 1))

    def answer(series: str, start: int, end: int) -> str | Exception:
        if start == 2007:
            return SourceRefusedError("bls: REQUEST_NOT_PROCESSED")
        return published._answer(series, start, end)

    client = FakeCpiClient(answer)
    raw, store = InMemoryReferenceRawStore(), InMemoryReferenceStore()

    result = await run(client, raw, store)

    assert result.outcome == "stopped"
    assert client.calls == [(SERIES, 1997, 2006), (SERIES, 2007, 2016)]
    assert [(r.start_year, r.end_year) for r in raw.list_cpi()] == [(1997, 2006)]
    assert store.read_table("cpi")["month"].max() == date(2006, 12, 1)


async def test_saved_name_is_utc() -> None:
    raw, store = InMemoryReferenceRawStore(), InMemoryReferenceStore()

    await run(FakeCpiClient.published_through(date(2026, 10, 1)), raw, store)

    assert {r.fetched_at for r in raw.list_cpi()} == {NOW}


async def test_malformed_response_not_saved(caplog: pytest.LogCaptureFixture) -> None:
    client = FakeCpiClient(lambda series, start, end: "<html>")
    raw, store = InMemoryReferenceRawStore(), InMemoryReferenceStore()

    with caplog.at_level(logging.WARNING):
        result = await run(client, raw, store)

    assert result.failed == 3
    assert raw.list_cpi() == []
    assert "1997-2006" in caplog.text


async def test_corrupt_saved_file_skipped_and_its_years_fetched_again(
    caplog: pytest.LogCaptureFixture,
) -> None:
    raw, store = InMemoryReferenceRawStore(), InMemoryReferenceStore()
    earlier = datetime(2026, 11, 1, tzinfo=UTC)
    raw.save_cpi(
        CpiResponse(
            source="bls",
            series=SERIES,
            fetched_at=earlier,
            start_year=1997,
            end_year=2006,
        ),
        "{",
    )
    client = FakeCpiClient.published_through(date(2026, 10, 1))

    with caplog.at_level(logging.WARNING):
        await run(client, raw, store)

    assert client.calls[0] == (SERIES, 1997, 2006)
    assert "won't parse" in caplog.text
    assert store.read_table("cpi")["month"].min() == date(1997, 1, 1)


async def test_swap_keeps_old_rows_and_fills_from_new() -> None:
    raw, store = InMemoryReferenceRawStore(), InMemoryReferenceStore()
    raw.save_cpi(
        CpiResponse(
            source="other",
            series=SERIES,
            fetched_at=datetime(2026, 1, 1, tzinfo=UTC),
            start_year=1997,
            end_year=1997,
        ),
        bls_body(SERIES, [date(1997, 1, 1)], value=1.0),
    )

    await run(FakeCpiClient.published_through(date(2026, 10, 1)), raw, store)

    jan_1997 = store.read_table("cpi").row(0, named=True)
    assert (jan_1997["source"], jan_1997["index_value"]) == ("bls", 100.0)


def test_cpi_needs_no_key_and_reports_an_unknown_source() -> None:
    from coffee_review.config import ReferenceSettings, Settings
    from coffee_review.pipeline.reference import UnknownSourceError, cpi

    settings = Settings(reference=ReferenceSettings(cpi_source="nope"))

    with pytest.raises(UnknownSourceError, match="bls"):
        cpi(settings, now=NOW)


async def test_a_bls_message_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    def answer(series: str, start: int, end: int) -> str:
        body = json.loads(bls_body(series, []))
        body["message"] = ["Series does not exist for Series CUUR0000SAX"]
        return json.dumps(body)

    raw, store = InMemoryReferenceRawStore(), InMemoryReferenceStore()

    with caplog.at_level(logging.WARNING):
        await run(FakeCpiClient(answer), raw, store)

    assert "Series does not exist" in caplog.text
