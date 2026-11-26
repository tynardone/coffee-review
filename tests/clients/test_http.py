"""Tests for coffee_review.clients.http: limits, retries, backoff and Retry-After.

No real network and no real waiting: requests go to an ``httpx.MockTransport``
and the ``recorded_sleeps`` fixture (tests/conftest.py) swaps out asyncio.sleep.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest

from coffee_review.clients.http import (
    BaseHttpClient,
    RequestAbandonedError,
    RetriesExhaustedError,
    _parse_retry_after,
)
from coffee_review.config import HttpClientSettings

BACKOFF_MAX = 5.0


def fast_settings() -> HttpClientSettings:
    """Two retries (three attempts), tiny backoff, effectively no rate limit."""
    return HttpClientSettings(
        base_url="https://example.test/",
        max_retries=2,
        backoff_base_seconds=0.01,
        backoff_max_seconds=BACKOFF_MAX,
        rate_per_sec=1000,
    )


class ScriptedServer:
    """A fake server that plays back a fixed list of replies, one per request.

    A reply is either an ``httpx.Response`` or an exception to raise.
    """

    def __init__(self, *replies: httpx.Response | Exception) -> None:
        self._replies = list(replies)
        self.calls = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def client(self) -> BaseHttpClient:
        return BaseHttpClient(
            fast_settings(), transport=httpx.MockTransport(self.handler)
        )


def http_date(offset_seconds: int) -> str:
    return format_datetime(
        datetime.now(UTC) + timedelta(seconds=offset_seconds), usegmt=True
    )


# --- _parse_retry_after ------------------------------------------------------


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("5", 5.0),
        (" 2.5 ", 2.5),
        ("-3", 0.0),
        (None, None),
        ("", None),
        ("garbage", None),
    ],
)
def test_parse_retry_after(header: str | None, expected: float | None) -> None:
    assert _parse_retry_after(header) == expected


def test_parse_retry_after_http_date_in_future() -> None:
    seconds = _parse_retry_after(http_date(10))

    assert seconds is not None
    assert 8 <= seconds <= 10


def test_parse_retry_after_http_date_in_past_is_zero() -> None:
    assert _parse_retry_after(http_date(-60)) == 0.0


# --- BaseHttpClient.request --------------------------------------------------


async def test_retries_then_succeeds(recorded_sleeps: list[float]) -> None:
    server = ScriptedServer(
        httpx.Response(503), httpx.Response(503), httpx.Response(200, text="ok")
    )

    async with server.client() as client:
        response = await client.request("GET", "page")

    assert response.text == "ok"
    assert server.calls == 3
    assert len(recorded_sleeps) == 2


async def test_raises_after_retries_exhausted(recorded_sleeps: list[float]) -> None:
    server = ScriptedServer(*(httpx.Response(503) for _ in range(3)))

    async with server.client() as client:
        with pytest.raises(RetriesExhaustedError) as excinfo:
            await client.request("GET", "page")

    assert excinfo.value.attempts == 3
    assert "HTTP 503" in excinfo.value.last_error
    assert excinfo.value.url == "https://example.test/page"


async def test_client_error_is_not_retried(recorded_sleeps: list[float]) -> None:
    server = ScriptedServer(httpx.Response(404))

    async with server.client() as client:
        with pytest.raises(httpx.HTTPStatusError):
            await client.request("GET", "missing")

    assert server.calls == 1
    assert recorded_sleeps == []


async def test_network_error_is_retried(recorded_sleeps: list[float]) -> None:
    server = ScriptedServer(
        httpx.ConnectError("refused"),
        httpx.ConnectError("refused"),
        httpx.Response(200),
    )

    async with server.client() as client:
        response = await client.request("GET", "page")

    assert response.status_code == 200
    assert server.calls == 3


async def test_retry_after_is_honored(recorded_sleeps: list[float]) -> None:
    server = ScriptedServer(
        httpx.Response(429, headers={"Retry-After": "3"}), httpx.Response(200)
    )

    async with server.client() as client:
        await client.request("GET", "page")

    assert recorded_sleeps[0] >= 3


async def test_retry_after_is_capped(recorded_sleeps: list[float]) -> None:
    server = ScriptedServer(
        httpx.Response(429, headers={"Retry-After": "3600"}), httpx.Response(200)
    )

    async with server.client() as client:
        await client.request("GET", "page")

    assert recorded_sleeps[0] == BACKOFF_MAX


async def test_rate_below_one_per_second_still_sends() -> None:
    # A limiter's capacity can't be below one request, or the first request
    # could never be let through (ADR 0005 allows rates such as 0.5/s).
    server = ScriptedServer(httpx.Response(200, text="ok"))
    slow = fast_settings().model_copy(update={"rate_per_sec": 0.5})
    transport = httpx.MockTransport(server.handler)

    async with BaseHttpClient(slow, transport=transport) as client:
        response = await client.request("GET", "page")

    assert response.text == "ok"


async def test_request_before_open_raises() -> None:
    client = ScriptedServer().client()

    with pytest.raises(RuntimeError, match="not open"):
        await client.request("GET", "page")


async def test_context_manager_closes_client() -> None:
    client = ScriptedServer().client()

    async with client:
        assert client._client is not None

    assert client._client is None


async def test_subclass_can_narrow_retryable_status(
    recorded_sleeps: list[float],
) -> None:
    class NoRetryClient(BaseHttpClient):
        retryable_status = frozenset()

    server = ScriptedServer(httpx.Response(503))
    client = NoRetryClient(
        fast_settings(), transport=httpx.MockTransport(server.handler)
    )

    async with client:
        with pytest.raises(httpx.HTTPStatusError):
            await client.request("GET", "page")

    assert server.calls == 1


class FakeClock:
    """A clock that moves only when a test (or fake server) advances it."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


async def test_stats_count_every_attempt_including_retries(
    recorded_sleeps: list[float],
) -> None:
    server = ScriptedServer(httpx.Response(503), httpx.Response(200))

    async with server.client() as client:
        await client.request("GET", "page")

    assert client.stats.requests == 2


async def test_stats_time_only_the_send(recorded_sleeps: list[float]) -> None:
    clock = FakeClock()

    def slow_handler(request: httpx.Request) -> httpx.Response:
        clock.now += 0.5  # the "network" takes half a second
        return httpx.Response(200)

    client = BaseHttpClient(
        fast_settings(), transport=httpx.MockTransport(slow_handler), clock=clock
    )
    async with client:
        await client.request("GET", "a")
        await client.request("GET", "b")

    assert client.stats.requests == 2
    assert client.stats.mean_response_seconds == pytest.approx(0.5)


def test_stats_mean_is_none_before_any_request() -> None:
    assert ScriptedServer().client().stats.mean_response_seconds is None


async def test_stop_prevents_further_retries(recorded_sleeps: list[float]) -> None:
    stop = asyncio.Event()
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        stop.set()  # pushback arrives elsewhere while this request is out
        return httpx.Response(500)

    client = BaseHttpClient(fast_settings(), transport=httpx.MockTransport(handler))
    async with client:
        with pytest.raises(RequestAbandonedError):
            await client.request("GET", "page", stop=stop)

    assert calls == 1


async def test_stop_during_backoff_ends_the_wait(recorded_sleeps: list[float]) -> None:
    stop = asyncio.Event()
    settings = fast_settings().model_copy(update={"backoff_base_seconds": 60})
    client = BaseHttpClient(
        settings, transport=httpx.MockTransport(lambda r: httpx.Response(500))
    )

    async def stop_soon() -> None:
        await asyncio.sleep(0)
        stop.set()

    async with client:
        with pytest.raises(RequestAbandonedError):
            await asyncio.wait_for(
                asyncio.gather(client.request("GET", "page", stop=stop), stop_soon()),
                timeout=2,
            )

    assert client.stats.requests == 1
