"""Tests for coffee_review.clients.forex_api."""

from __future__ import annotations

import json
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
from coffee_review.config import ForexApiSettings

KEY = "test-key"


def capture_requests(
    status: int = 200,
) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    """A fake transport that records each request and answers ``status``."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json={})

    return httpx.MockTransport(handler), seen


def settings_with_key(key: str | None = KEY) -> ForexApiSettings:
    return ForexApiSettings(api_key=SecretStr(key) if key is not None else None)


async def test_key_sent_in_authorization_header() -> None:
    transport, seen = capture_requests()

    async with ForexApiClient(settings_with_key(), transport=transport) as client:
        await client.request("GET", "historical/2020-01-01.json")

    assert seen[0].headers["Authorization"] == f"Token {KEY}"


async def test_key_not_in_url() -> None:
    # URLs end up in logs and error messages; the key must never be in one.
    transport, seen = capture_requests()

    async with ForexApiClient(settings_with_key(), transport=transport) as client:
        await client.request("GET", "historical/2020-01-01.json")

    assert KEY not in str(seen[0].url)


async def test_key_not_in_error_message() -> None:
    transport, _ = capture_requests(status=401)

    async with ForexApiClient(settings_with_key(), transport=transport) as client:
        with pytest.raises(httpx.HTTPStatusError) as excinfo:
            await client.request("GET", "latest.json")

    assert KEY not in str(excinfo.value)


async def test_caller_params_are_sent() -> None:
    transport, seen = capture_requests()

    async with ForexApiClient(settings_with_key(), transport=transport) as client:
        await client.request(
            "GET", "historical/2020-01-01.json", params={"base": "USD"}
        )

    assert seen[0].url.params["base"] == "USD"


def test_missing_key_raises() -> None:
    with pytest.raises(MissingApiKeyError):
        ForexApiClient(settings_with_key(None))


# --- fetch_day: an answer comes back as text; anything else is a typed error


def fast_settings() -> ForexApiSettings:
    return ForexApiSettings(
        api_key=SecretStr(KEY),
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


async def test_fetch_day_requests_the_historical_path() -> None:
    transport, seen = answering(200, {"base": "USD", "rates": {}})

    async with ForexApiClient(fast_settings(), transport=transport) as client:
        text = await client.fetch_day(date(2020, 3, 1))

    assert seen[0].method == "GET"
    assert seen[0].url.path == "/api/historical/2020-03-01.json"
    assert json.loads(text) == {"base": "USD", "rates": {}}


async def test_fetch_day_returns_a_400_body() -> None:
    body = {"error": True, "status": 400, "message": "not_available"}
    transport, _ = answering(400, body)

    async with ForexApiClient(fast_settings(), transport=transport) as client:
        text = await client.fetch_day(date(1990, 1, 1))

    assert json.loads(text) == body


async def test_401_is_auth_error_without_the_key() -> None:
    transport, _ = answering(401, {"error": True, "message": "invalid_app_id"})

    async with ForexApiClient(fast_settings(), transport=transport) as client:
        with pytest.raises(SourceAuthError) as excinfo:
            await client.fetch_day(date(2020, 1, 1))

    assert "invalid_app_id" in str(excinfo.value)
    assert KEY not in str(excinfo.value)


@pytest.mark.parametrize("status", [403, 429])
async def test_refused_and_not_retried(status: int) -> None:
    transport, seen = answering(status, {"error": True, "message": "not_allowed"})

    async with ForexApiClient(fast_settings(), transport=transport) as client:
        with pytest.raises(SourceRefusedError):
            await client.fetch_day(date(2020, 1, 1))

    assert len(seen) == 1


async def test_other_4xx_is_unavailable() -> None:
    transport, _ = answering(404, {"error": True, "message": "not_found"})

    async with ForexApiClient(fast_settings(), transport=transport) as client:
        with pytest.raises(SourceUnavailableError):
            await client.fetch_day(date(2020, 1, 1))


async def test_exhausted_5xx_is_unavailable() -> None:
    transport, seen = answering(503, {})

    async with ForexApiClient(fast_settings(), transport=transport) as client:
        with pytest.raises(SourceUnavailableError):
            await client.fetch_day(date(2020, 1, 1))

    assert len(seen) == 2  # first try + max_retries=1
