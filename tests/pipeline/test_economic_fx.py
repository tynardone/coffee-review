"""Tests for the fx stage: the real client over a fake network, a store in a
temp folder."""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import TYPE_CHECKING

import httpx
import pytest

from coffee_review.clients.openexchangerates import MissingApiKeyError
from coffee_review.config import Settings
from coffee_review.pipeline.economic import EconomicRunResult, fx, run_fx
from coffee_review.storage.economic_store import EconomicStore
from tests.economic_samples import NOT_AVAILABLE, fx_client, oxr_body

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from tests.economic_samples import Reply

START = date(2026, 8, 1)
TODAY = date(2026, 11, 22)
ALL_DAYS = [date(2026, 8, 1), date(2026, 9, 1), date(2026, 10, 1), date(2026, 11, 1)]


def every_day(_day: date) -> Reply:
    return oxr_body()


async def run(
    store: EconomicStore, answer: Callable[[date], Reply] = every_day
) -> tuple[EconomicRunResult, list[date]]:
    client, asked = fx_client(answer)
    result = await run_fx(client=client, store=store, start=START, today=TODAY)
    return result, asked


async def test_first_run_fetches_every_missing_first_then_rerun_fetches_nothing(
    tmp_path: Path,
) -> None:
    store = EconomicStore(tmp_path)

    first, asked_first = await run(store)
    again, asked_again = await run(store)

    assert asked_first == ALL_DAYS
    assert asked_again == []
    assert (first.outcome, first.fetched, first.already) == ("finished", 4, 0)
    assert (again.fetched, again.already) == (0, 4)
    assert store.read_table("fx")["day"].unique().sort().to_list() == ALL_DAYS
    assert (again.rows, again.first, again.last) == (8, ALL_DAYS[0], ALL_DAYS[-1])


async def test_not_available_is_saved_as_a_gap_and_not_asked_again(
    tmp_path: Path,
) -> None:
    store = EconomicStore(tmp_path)

    def answer(day: date) -> Reply:
        if day == date(2026, 9, 1):
            return httpx.Response(400, text=NOT_AVAILABLE)
        return oxr_body()

    first, _ = await run(store, answer)
    _, asked_again = await run(store, answer)

    assert first.gaps == ("2026-09-01",)
    assert asked_again == []
    assert date(2026, 9, 1) not in store.read_table("fx")["day"].to_list()


@pytest.mark.parametrize("status", [401, 403, 429, 503])
async def test_stops_keeps_earlier_files_and_rebuilds(
    tmp_path: Path, status: int
) -> None:
    store = EconomicStore(tmp_path)

    def answer(day: date) -> Reply:
        if day == date(2026, 9, 1):
            return httpx.Response(status, json={"error": True, "message": "no"})
        return oxr_body()

    result, asked = await run(store, answer)

    assert result.outcome == "stopped"
    assert asked[-1] == date(2026, 9, 1)
    assert date(2026, 10, 1) not in asked
    assert store.fx_days() == [date(2026, 8, 1)]
    assert store.read_table("fx")["day"].unique().to_list() == [date(2026, 8, 1)]


async def test_next_run_carries_on_after_a_stop(tmp_path: Path) -> None:
    store = EconomicStore(tmp_path)
    await run(
        store,
        lambda day: httpx.Response(429) if day == date(2026, 9, 1) else oxr_body(),
    )

    _, asked = await run(store)

    assert asked == ALL_DAYS[1:]


async def test_malformed_answer_not_saved_and_asked_again(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = EconomicStore(tmp_path)

    with caplog.at_level(logging.WARNING):
        first, _ = await run(
            store, lambda day: "<html>" if day == date(2026, 9, 1) else oxr_body()
        )
    _, asked_again = await run(store)

    assert (first.outcome, first.failed) == ("finished", 1)
    assert "2026-09-01" in caplog.text
    assert asked_again == [date(2026, 9, 1)]


async def test_stops_after_three_answers_in_a_row_that_wont_parse(
    tmp_path: Path,
) -> None:
    store = EconomicStore(tmp_path)
    eur = json.dumps({"base": "EUR", "rates": {"USD": 1.1}})

    result, asked = await run(store, lambda _day: eur)

    assert result.outcome == "stopped"
    assert len(asked) == 3
    assert result.reason is not None and "'EUR'" in result.reason
    assert store.fx_days() == []


async def test_corrupt_saved_file_is_fetched_again_and_replaced(
    tmp_path: Path,
) -> None:
    store = EconomicStore(tmp_path)
    for day in ALL_DAYS:
        store.save_fx(day, oxr_body())
    store.save_fx(date(2026, 9, 1), "{")

    _, asked = await run(store)

    assert asked == [date(2026, 9, 1)]
    assert store.read_fx(date(2026, 9, 1)) == oxr_body()


async def test_nothing_wanted_before_start(tmp_path: Path) -> None:
    client, asked = fx_client(every_day)

    result = await run_fx(
        client=client, store=EconomicStore(tmp_path), start=START, today=START
    )

    assert asked == []
    assert (result.rows, result.first, result.last) == (0, None, None)


def test_missing_key_fails_before_any_request() -> None:
    with pytest.raises(MissingApiKeyError):
        fx(Settings(), today=TODAY)


def result(**changes: object) -> EconomicRunResult:
    fields: dict[str, object] = {
        "kind": "fx",
        "outcome": "finished",
        "reason": None,
        "fetched": 3,
        "already": 201,
        "failed": 0,
        "gaps": (),
        "rows": 34170,
        "first": date(2010, 1, 1),
        "last": date(2026, 11, 1),
        "location": "data/economic/fx.parquet",
    }
    return EconomicRunResult(**(fields | changes))  # type: ignore[arg-type]


def test_describe_finished() -> None:
    assert result().describe() == (
        "fx finished: 3 fetched, 201 already stored, 0 failed"
        " · fx.parquet 34,170 rows, 2010-01-01 to 2026-11-01"
    )


def test_describe_gaps_and_stop() -> None:
    line = result(
        outcome="stopped",
        reason="openexchangerates: HTTP 429",
        gaps=tuple(f"2020-0{n}-01" for n in range(1, 8)),
    ).describe()

    assert line == (
        "fx stopped: 3 fetched, 201 already stored, 0 failed, 7 gaps"
        " (2020-01-01, 2020-02-01, 2020-03-01, 2020-04-01, 2020-05-01, …)"
        " (stopped: openexchangerates: HTTP 429)"
        " · fx.parquet 34,170 rows, 2010-01-01 to 2026-11-01"
    )


def test_describe_one_gap_and_empty_table() -> None:
    line = result(kind="cpi", gaps=("2025-10",), rows=0, first=None, last=None)

    assert "1 gap (2025-10)" in line.describe()
    assert line.describe().endswith("· fx.parquet 0 rows")
