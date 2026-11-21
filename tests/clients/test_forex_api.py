"""Tests for coffee_review.clients.forex_api."""

from __future__ import annotations

import httpx
import pytest
from pydantic import SecretStr

from coffee_review.clients.forex_api import ForexApiClient, MissingApiKeyError
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
