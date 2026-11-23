"""Tests for the fx stage in coffee_review.pipeline.reference."""

from __future__ import annotations

import json
import logging
from datetime import date

import httpx
import pytest
from pydantic import SecretStr

from coffee_review.clients.forex_api import ForexApiClient, MissingApiKeyError
from coffee_review.clients.reference import (
    SourceAuthError,
    SourceRefusedError,
    SourceUnavailableError,
)
from coffee_review.config import ForexApiSettings, ReferenceSettings, Settings
from coffee_review.models.reference import FxResponse
from coffee_review.parsers.fx_openexchangerates import parse_fx_openexchangerates
from coffee_review.pipeline.reference import (
    FX_SOURCES,
    FxSource,
    ReferenceRunResult,
    UnknownSourceError,
    fx,
    run_fx,
)
from tests.fakes import (
    FakeFxClient,
    InMemoryReferenceRawStore,
    InMemoryReferenceStore,
    oxr_body,
)

START = date(2026, 8, 1)
TODAY = date(2026, 11, 22)
ALL_DAYS = [date(2026, 8, 1), date(2026, 9, 1), date(2026, 10, 1), date(2026, 11, 1)]
NOT_AVAILABLE = json.dumps({"error": True, "status": 400, "message": "not_available"})


def source() -> FxSource:
    return FxSource(
        client=lambda _settings: FakeFxClient.answering_every_day(),
        parse=parse_fx_openexchangerates,
    )


async def run(
    client: FakeFxClient,
    raw: InMemoryReferenceRawStore,
    store: InMemoryReferenceStore,
    *,
    name: str = "s",
    today: date = TODAY,
) -> ReferenceRunResult:
    return await run_fx(
        source_name=name,
        sources={"s": source(), "a": source(), "b": source()},
        client=client,
        raw=raw,
        store=store,
        start=START,
        today=today,
    )


async def test_first_run_fetches_every_missing_first_then_rerun_fetches_nothing() -> (
    None
):
    client = FakeFxClient.answering_every_day()
    raw, store = InMemoryReferenceRawStore(), InMemoryReferenceStore()

    first = await run(client, raw, store)
    again = await run(client, raw, store)

    assert client.calls == ALL_DAYS
    assert (first.outcome, first.fetched, first.already) == ("finished", 4, 0)
    assert (again.requests, again.fetched, again.already) == (0, 0, 4)
    assert store.read_table("fx")["day"].unique().sort().to_list() == ALL_DAYS
    assert again.rows == 8  # USD and TWD on 4 days
    assert (again.first, again.last) == (date(2026, 8, 1), date(2026, 11, 1))


async def test_zero_requests_on_rerun_with_the_real_client() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path)
        return httpx.Response(200, text=oxr_body())

    settings = ForexApiSettings(api_key=SecretStr("k"), rate_per_sec=1000)
    raw, store = InMemoryReferenceRawStore(), InMemoryReferenceStore()

    for _ in range(2):
        client = ForexApiClient(settings, transport=httpx.MockTransport(handler))
        await run_fx(
            source_name="openexchangerates",
            sources=FX_SOURCES,
            client=client,
            raw=raw,
            store=store,
            start=START,
            today=TODAY,
        )

    assert requested == [f"/api/historical/{d}.json" for d in ALL_DAYS]


async def test_not_available_saved_as_gap_and_not_asked_again() -> None:
    client = FakeFxClient.answering({date(2026, 9, 1): NOT_AVAILABLE})
    raw, store = InMemoryReferenceRawStore(), InMemoryReferenceStore()

    first = await run(client, raw, store)
    await run(client, raw, store)

    assert client.calls == ALL_DAYS
    assert first.gaps == 1
    assert store.read_table("fx_gaps")["day"].to_list() == [date(2026, 9, 1)]
    assert date(2026, 9, 1) not in store.read_table("fx")["day"].to_list()


@pytest.mark.parametrize(
    "error",
    [
        SourceRefusedError("s: HTTP 429"),
        SourceAuthError("s: HTTP 401 invalid_app_id"),
        SourceUnavailableError("s: retries exhausted"),
    ],
)
async def test_stops_keeps_earlier_files_and_rebuilds(error: Exception) -> None:
    client = FakeFxClient.answering({date(2026, 9, 1): error})
    raw, store = InMemoryReferenceRawStore(), InMemoryReferenceStore()

    result = await run(client, raw, store)

    assert result.outcome == "stopped"
    assert result.reason == str(error)
    assert client.calls == [date(2026, 8, 1), date(2026, 9, 1)]
    assert [r.day for r in raw.list_fx()] == [date(2026, 8, 1)]
    assert store.read_table("fx")["day"].unique().to_list() == [date(2026, 8, 1)]


async def test_next_run_carries_on_after_a_stop() -> None:
    raw, store = InMemoryReferenceRawStore(), InMemoryReferenceStore()
    await run(
        FakeFxClient.answering({date(2026, 9, 1): SourceRefusedError("x")}), raw, store
    )

    client = FakeFxClient.answering_every_day()
    await run(client, raw, store)

    assert client.calls == ALL_DAYS[1:]


async def test_malformed_response_not_saved_and_retried_next_run(
    caplog: pytest.LogCaptureFixture,
) -> None:
    raw, store = InMemoryReferenceRawStore(), InMemoryReferenceStore()

    with caplog.at_level(logging.WARNING):
        first = await run(
            FakeFxClient.answering({date(2026, 9, 1): "<html>oops</html>"}), raw, store
        )
    client = FakeFxClient.answering_every_day()
    await run(client, raw, store)

    assert first.outcome == "finished"
    assert first.failed == 1
    assert "2026-09-01" in caplog.text
    assert client.calls == [date(2026, 9, 1)]


async def test_corrupt_saved_file_is_refetched_and_replaced() -> None:
    raw, store = InMemoryReferenceRawStore(), InMemoryReferenceStore()
    for day in ALL_DAYS:
        raw.save_fx(FxResponse(source="s", day=day), oxr_body())
    raw.save_fx(FxResponse(source="s", day=date(2026, 9, 1)), "{")
    client = FakeFxClient.answering_every_day()

    result = await run(client, raw, store)

    assert client.calls == [date(2026, 9, 1)]
    assert raw.read_fx(FxResponse(source="s", day=date(2026, 9, 1))) == oxr_body()
    assert date(2026, 9, 1) in store.read_table("fx")["day"].to_list()
    assert result.failed == 0


async def test_swap_fills_only_what_the_new_source_lacks() -> None:
    raw, store = InMemoryReferenceRawStore(), InMemoryReferenceStore()
    await run(FakeFxClient.answering_every_day(), raw, store, name="a")
    raw.save_fx(FxResponse(source="b", day=date(2026, 8, 1)), oxr_body())

    client = FakeFxClient.answering_every_day()
    await run(client, raw, store, name="b")

    assert client.calls == ALL_DAYS[1:]
    assert set(store.read_table("fx")["source"].to_list()) == {"b"}


async def test_dropped_values_are_counted() -> None:
    client = FakeFxClient.answering({}, default=oxr_body({"USD": 1, "VEF": 0}))
    raw, store = InMemoryReferenceRawStore(), InMemoryReferenceStore()

    result = await run(client, raw, store)

    assert result.dropped == 4
    assert "VEF" not in store.read_table("fx")["currency"].to_list()


async def test_before_start_nothing_and_empty_tables() -> None:
    client = FakeFxClient.answering_every_day()
    raw, store = InMemoryReferenceRawStore(), InMemoryReferenceStore()

    result = await run(client, raw, store, today=START)

    assert client.calls == []
    assert result.rows == 0
    assert (result.first, result.last) == (None, None)
    assert store.read_table("fx").is_empty()


def test_unknown_source_fails_before_any_client() -> None:
    settings = Settings(reference=ReferenceSettings(fx_source="nope"))

    with pytest.raises(UnknownSourceError, match="openexchangerates"):
        fx(settings, today=TODAY)


def test_missing_key_fails_before_any_request() -> None:
    with pytest.raises(MissingApiKeyError):
        fx(Settings(), today=TODAY)


def result(**changes: object) -> ReferenceRunResult:
    fields: dict[str, object] = {
        "kind": "fx",
        "outcome": "finished",
        "reason": None,
        "requests": 3,
        "fetched": 3,
        "already": 201,
        "failed": 0,
        "gaps": 0,
        "dropped": 0,
        "rows": 34170,
        "first": date(2010, 1, 1),
        "last": date(2026, 11, 1),
        "location": "data/reference/fx.parquet",
    }
    return ReferenceRunResult(**(fields | changes))  # type: ignore[arg-type]


def test_describe_finished() -> None:
    assert result().describe() == (
        "fx finished: 3 fetched, 201 already stored, 0 failed"
        " · fx.parquet 34,170 rows, 2010-01-01 to 2026-11-01"
    )


def test_describe_with_gaps_drops_and_stop() -> None:
    line = result(
        outcome="stopped", reason="openexchangerates: HTTP 429", gaps=2, dropped=5
    ).describe()

    assert line == (
        "fx stopped: 3 fetched, 201 already stored, 0 failed, 2 gaps,"
        " 5 values dropped (stopped: openexchangerates: HTTP 429)"
        " · fx.parquet 34,170 rows, 2010-01-01 to 2026-11-01"
    )


def test_describe_empty_table() -> None:
    line = result(rows=0, first=None, last=None).describe()

    assert line.endswith("· fx.parquet 0 rows")


async def test_saved_files_of_an_unknown_source_skipped_with_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    raw, store = InMemoryReferenceRawStore(), InMemoryReferenceStore()
    raw.save_fx(FxResponse(source="gone", day=date(2026, 8, 1)), oxr_body())

    with caplog.at_level(logging.WARNING):
        await run(FakeFxClient.answering_every_day(), raw, store)

    assert "gone" in caplog.text
    assert "gone" not in store.read_table("fx")["source"].to_list()


async def test_unknown_configured_source_fails_before_any_request() -> None:
    client = FakeFxClient.answering_every_day()

    with pytest.raises(UnknownSourceError):
        await run(
            client, InMemoryReferenceRawStore(), InMemoryReferenceStore(), name="x"
        )

    assert client.calls == []


def test_describe_names_gap_periods() -> None:
    one = result(kind="cpi", gaps=1, gap_periods=("2025-10",)).describe()
    many = result(gaps=7, gap_periods=tuple(f"2020-0{n}" for n in range(1, 8)))

    assert "1 gap (2025-10)," not in one
    assert "1 gap (2025-10)" in one
    assert "7 gaps (2020-01, 2020-02, 2020-03, 2020-04, 2020-05, …)" in many.describe()
