"""Tests for the cpi stage: the real client over a fake network, a store in a
temp folder."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import httpx

from coffee_review.models.economic import CpiFile
from coffee_review.pipeline.economic import EconomicRunResult, run_cpi
from coffee_review.storage.economic_store import EconomicStore
from tests.economic_samples import SERIES, bls_body, bls_client, published_through

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    import pytest

    from tests.economic_samples import Reply

START = date(1997, 1, 1)
NOW = datetime(2026, 11, 22, 12, tzinfo=UTC)
OCT_2025 = date(2025, 10, 1)
THROUGH_OCTOBER = published_through(date(2026, 10, 1), unavailable=[OCT_2025])


async def run(
    store: EconomicStore,
    answer: Callable[[int, int], Reply] = THROUGH_OCTOBER,
    now: datetime = NOW,
) -> tuple[EconomicRunResult, list[tuple[int, int]]]:
    client, asked = bls_client(answer)
    result = await run_cpi(
        client=client, store=store, series=SERIES, start=START, now=now
    )
    return result, asked


async def test_first_run_three_requests_then_zero(tmp_path: Path) -> None:
    store = EconomicStore(tmp_path)

    first, asked_first = await run(store)
    again, asked_again = await run(store, now=NOW.replace(hour=13))

    assert asked_first == [(1997, 2006), (2007, 2016), (2017, 2026)]
    assert asked_again == []
    assert (first.fetched, first.gaps) == (3, ("2025-10",))
    assert again.gaps == ("2025-10",)
    table = store.read_table("cpi")
    assert table.height == 12 * 29 + 10 - 1  # 1997-01 to 2026-10, without 2025-10
    assert OCT_2025 not in table["month"].to_list()


async def test_unavailable_month_not_asked_again_next_month(tmp_path: Path) -> None:
    store = EconomicStore(tmp_path)
    await run(store)

    _, asked = await run(
        store,
        published_through(date(2026, 11, 1), unavailable=[OCT_2025]),
        now=datetime(2026, 12, 20, 9, tzinfo=UTC),
    )

    assert asked == [(2026, 2026)]


async def test_newest_month_asked_once_a_day(tmp_path: Path) -> None:
    store = EconomicStore(tmp_path)
    await run(store, published_through(date(2026, 9, 1)))

    _, same_day = await run(store, published_through(date(2026, 9, 1)))
    _, next_day = await run(
        store,
        published_through(date(2026, 10, 1)),
        now=datetime(2026, 11, 23, 8, tzinfo=UTC),
    )

    assert same_day == []
    assert next_day == [(2026, 2026)]
    assert store.read_table("cpi")["month"].max() == date(2026, 10, 1)


async def test_newest_response_wins(tmp_path: Path) -> None:
    store = EconomicStore(tmp_path)
    store.save_cpi(
        SERIES,
        CpiFile(
            fetched_at=datetime(2026, 1, 1, tzinfo=UTC), start_year=2026, end_year=2026
        ),
        bls_body([date(2026, 1, 1)], value=1.0),
    )

    await run(store, lambda start, end: bls_body([date(2026, 1, 1)], value=2.0))

    jan = store.read_table("cpi").filter(
        store.read_table("cpi")["month"] == date(2026, 1, 1)
    )
    assert jan["index_value"].to_list() == [2.0]


async def test_daily_limit_stops_with_saved_kept(tmp_path: Path) -> None:
    store = EconomicStore(tmp_path)
    refused = httpx.Response(
        200, json={"status": "REQUEST_NOT_PROCESSED", "message": ["daily threshold"]}
    )

    def answer(start: int, end: int) -> Reply:
        return refused if start == 2007 else THROUGH_OCTOBER(start, end)

    result, asked = await run(store, answer)

    assert result.outcome == "stopped"
    assert asked == [(1997, 2006), (2007, 2016)]
    assert [(f.start_year, f.end_year) for f in store.cpi_files(SERIES)] == [
        (1997, 2006)
    ]
    assert store.read_table("cpi")["month"].max() == date(2006, 12, 1)


async def test_saved_name_is_utc(tmp_path: Path) -> None:
    store = EconomicStore(tmp_path)

    await run(store)

    assert {f.fetched_at for f in store.cpi_files(SERIES)} == {NOW}


async def test_a_bls_message_is_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = EconomicStore(tmp_path)

    with caplog.at_level(logging.WARNING):
        await run(
            store,
            lambda start, end: bls_body([], message=["Series does not exist"]),
        )

    assert "Series does not exist" in caplog.text


async def test_corrupt_saved_file_skipped_and_its_years_fetched_again(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = EconomicStore(tmp_path)
    store.save_cpi(
        SERIES,
        CpiFile(
            fetched_at=datetime(2026, 11, 1, tzinfo=UTC), start_year=1997, end_year=2006
        ),
        "{",
    )

    with caplog.at_level(logging.WARNING):
        _, asked = await run(store)

    assert asked[0] == (1997, 2006)
    assert "won't parse" in caplog.text
    assert store.read_table("cpi")["month"].min() == date(1997, 1, 1)
