"""Tests for coffee_review.clients.bls."""

from __future__ import annotations

import json

import httpx
import pytest

from coffee_review.clients.bls import BlsClient
from coffee_review.clients.economic import (
    SourceRefusedError,
    SourceUnavailableError,
)
from coffee_review.config import BlsSettings

SERIES = "CUUR0000SA0"
SUCCEEDED: dict[str, object] = {
    "status": "REQUEST_SUCCEEDED",
    "message": [],
    "Results": {"series": []},
}


def fast_bls() -> BlsSettings:
    return BlsSettings(
        rate_per_sec=1000,
        max_retries=1,
        backoff_base_seconds=0.001,
        backoff_max_seconds=0.001,
    )


def answering(
    status: int, body: dict[str, object]
) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=body)

    return httpx.MockTransport(handler), seen


async def test_posts_series_and_years() -> None:
    transport, seen = answering(200, SUCCEEDED)

    async with BlsClient(fast_bls(), transport=transport) as client:
        text = await client.fetch_years(SERIES, 1997, 2006)

    assert seen[0].method == "POST"
    assert seen[0].url.path == "/publicAPI/v1/timeseries/data/"
    assert json.loads(seen[0].content) == {
        "seriesid": [SERIES],
        "startyear": "1997",
        "endyear": "2006",
    }
    assert json.loads(text) == SUCCEEDED


async def test_answer_with_no_data_comes_back() -> None:
    body: dict[str, object] = {**SUCCEEDED, "message": ["No Data Available for Series"]}
    transport, _ = answering(200, body)

    async with BlsClient(fast_bls(), transport=transport) as client:
        text = await client.fetch_years(SERIES, 2030, 2030)

    assert json.loads(text) == body


async def test_daily_limit_is_refused() -> None:
    body: dict[str, object] = {
        "status": "REQUEST_NOT_PROCESSED",
        "message": ["daily threshold for total number of requests"],
        "Results": {},
    }
    transport, _ = answering(200, body)

    async with BlsClient(fast_bls(), transport=transport) as client:
        with pytest.raises(SourceRefusedError, match="daily threshold"):
            await client.fetch_years(SERIES, 2020, 2020)


async def test_429_is_refused_and_not_retried() -> None:
    transport, seen = answering(429, {})

    async with BlsClient(fast_bls(), transport=transport) as client:
        with pytest.raises(SourceRefusedError):
            await client.fetch_years(SERIES, 2020, 2020)

    assert len(seen) == 1


async def test_exhausted_5xx_is_unavailable() -> None:
    transport, seen = answering(500, {})

    async with BlsClient(fast_bls(), transport=transport) as client:
        with pytest.raises(SourceUnavailableError):
            await client.fetch_years(SERIES, 2020, 2020)

    assert len(seen) == 2
