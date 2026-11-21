"""Tests for coffee_review.clients.forex_api."""

from __future__ import annotations

import httpx
import pytest
from pydantic import SecretStr

from coffee_review.clients.forex_api import ForexApiClient, MissingApiKeyError
from coffee_review.config import ForexApiSettings


def capture_requests() -> tuple[httpx.MockTransport, list[httpx.Request]]:
    """A fake transport that records each request and answers 200 with ``{}``."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    return httpx.MockTransport(handler), seen


def settings_with_key(key: str | None = "test-key") -> ForexApiSettings:
    return ForexApiSettings(api_key=SecretStr(key) if key is not None else None)


async def test_app_id_sent_as_query_param() -> None:
    transport, seen = capture_requests()

    async with ForexApiClient(settings_with_key(), transport=transport) as client:
        await client.request("GET", "historical/2020-01-01.json")

    assert seen[0].url.params["app_id"] == "test-key"


async def test_caller_params_are_merged_with_app_id() -> None:
    transport, seen = capture_requests()

    async with ForexApiClient(settings_with_key(), transport=transport) as client:
        await client.request(
            "GET", "historical/2020-01-01.json", params={"base": "USD"}
        )

    assert seen[0].url.params["base"] == "USD"
    assert seen[0].url.params["app_id"] == "test-key"


def test_missing_key_raises() -> None:
    with pytest.raises(MissingApiKeyError):
        ForexApiClient(settings_with_key(None))
