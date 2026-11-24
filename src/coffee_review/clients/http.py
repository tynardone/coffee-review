"""Shared async HTTP plumbing for every client.

A concurrency cap, a rate limit, retries with exponential backoff and full
jitter, and Retry-After handling.

Service clients subclass ``BaseHttpClient`` and call ``self.request(...)``.
Nothing outside of coffee_review.clients should import this module.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Any, ClassVar, Self

import httpx
from aiolimiter import AsyncLimiter

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import TracebackType

    from coffee_review.config import HttpClientSettings

logger = logging.getLogger(__name__)

RETRYABLE_STATUS: frozenset[int] = frozenset({408, 425, 429, 500, 502, 503, 504})
RETRYABLE_EXCEPTIONS: tuple[type[Exception], ...] = (
    httpx.TimeoutException,
    httpx.NetworkError,
    httpx.RemoteProtocolError,
)


class RetriesExhaustedError(Exception):
    """Raised when a request still fails after every retry."""

    def __init__(self, method: str, url: str, attempts: int, last_error: str):
        self.method = method
        self.url = url
        self.attempts = attempts
        self.last_error = last_error
        super().__init__(
            f"{method} {url} failed after {attempts} attempts: {last_error}"
        )


class RequestAbandonedError(Exception):
    """Raised when a stop signal arrives before a retry could be sent."""

    def __init__(self, method: str, url: str, attempts: int, last_error: str):
        self.method = method
        self.url = url
        self.attempts = attempts
        self.last_error = last_error
        super().__init__(
            f"{method} {url} stopped after {attempts} attempt(s): {last_error}"
        )


def _make_limiter(rate_per_sec: float) -> AsyncLimiter:
    """Build a limiter that also works for rates below one per second."""
    capacity = max(1.0, rate_per_sec)
    return AsyncLimiter(max_rate=capacity, time_period=capacity / rate_per_sec)


def _parse_retry_after(value: str | None) -> float | None:
    """Returns Retry-After in seconds. Handles both seconds and HTTP-date forms."""
    if not value:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except TypeError, ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - datetime.now(UTC)).total_seconds())


@dataclass
class RequestStats:
    """Every attempt this client sent, and how long the site took to answer.

    Response time is measured around the send only, not the wait for a
    concurrency or rate-limit slot, so it reflects the site, not our limits.
    """

    requests: int = 0
    response_seconds: float = 0.0

    @property
    def mean_response_seconds(self) -> float | None:
        """The average time a request took, or None if none was sent."""
        return self.response_seconds / self.requests if self.requests else None


class BaseHttpClient:
    """Async HTTP client with limits and retries.

    Use it as an async context manager::

    async with SomeClient(settings) as client:
        await client.some_method()

    Subclasses can narrow ``retryable_status`` when some statuses mean "stop",
    not "try again" (the coffeereview.com scraper treats 429 as pushback).
    """

    retryable_status: ClassVar[frozenset[int]] = RETRYABLE_STATUS

    def __init__(
        self,
        settings: HttpClientSettings,
        *,
        headers: dict[str, str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings
        self.stats = RequestStats()
        self._clock = clock  # lets tests control measured response times
        self._semaphore = asyncio.Semaphore(settings.max_concurrency)
        self._limiter = _make_limiter(settings.rate_per_sec)
        self._headers = {"User-Agent": settings.user_agent, **(headers or {})}
        self._transport = transport  # lets tests pass httpx.MockTransport
        self._client: httpx.AsyncClient | None = None

    async def open(self) -> None:
        """Open the connection pool; ``async with`` does this for you."""
        if self._client is not None:
            return
        s = self.settings
        self._client = httpx.AsyncClient(
            base_url=str(s.base_url).rstrip("/") + "/",
            headers=self._headers,
            timeout=httpx.Timeout(s.timeout_seconds, connect=s.connect_timeout_seconds),
            limits=httpx.Limits(
                max_connections=s.max_concurrency,
                max_keepalive_connections=s.max_concurrency,
            ),
            follow_redirects=True,
            transport=self._transport,
        )

    async def close(self) -> None:
        """Close the connection pool; ``async with`` does this for you."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _backoff_delay(self, attempt: int) -> float:
        """Exponential backoff with full jitter.

        A uniform draw from ``0`` to ``min(max, base * 2**attempt)``.
        """
        s = self.settings
        cap = min(s.backoff_max_seconds, s.backoff_base_seconds * 2**attempt)
        return random.uniform(0, cap)

    async def _send_once(
        self, client: httpx.AsyncClient, method: str, path: str, **kwargs: Any
    ) -> tuple[httpx.Response | None, str]:
        """One attempt, inside the concurrency and rate limits, counted and timed.

        Returns the response, or None and the error when a retryable network
        error happened instead.
        """
        async with self._semaphore, self._limiter:
            sent_at = self._clock()
            try:
                return await client.request(method, path, **kwargs), ""
            except RETRYABLE_EXCEPTIONS as exc:
                return None, f"{type(exc).__name__}: {exc}"
            finally:
                self.stats.requests += 1
                self.stats.response_seconds += self._clock() - sent_at

    def _retry_delay(self, attempt: int, resp: httpx.Response | None) -> float:
        """Backoff with jitter, lengthened to honour Retry-After (up to the cap)."""
        delay = self._backoff_delay(attempt)
        if resp is not None:
            retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
            if retry_after is not None:
                delay = max(delay, min(retry_after, self.settings.backoff_max_seconds))
        return delay

    async def __aenter__(self) -> Self:
        await self.open()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def request(
        self,
        method: str,
        path: str,
        *,
        stop: asyncio.Event | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        """Send a request, retrying retryable failures with backoff.

        Args:
            method: The HTTP method.
            path: The path, relative to the base URL (or a full URL).
            stop: When given and set, nothing more is sent: no further attempt
                starts, and a backoff wait ends early.
            **kwargs: Passed on to ``httpx.AsyncClient.request``.

        Returns:
            The response, for any status that isn't retried and isn't a 4xx or 5xx.

        Raises:
            httpx.HTTPStatusError: For an error status that isn't retried.
            RetriesExhaustedError: When every attempt failed.
            RequestAbandonedError: When ``stop`` was set before a retry.
            RuntimeError: If the client isn't open.
        """
        client = self._client
        if client is None:
            raise RuntimeError("Client not open; use 'async with' or call open()")
        s = self.settings
        url = str(client.base_url.join(path))  # full URL for logs and errors
        last_error = "unknown"

        for attempt in range(s.max_retries + 1):
            if stop is not None and stop.is_set():
                raise RequestAbandonedError(method, url, attempt, last_error)

            resp, error = await self._send_once(client, method, path, **kwargs)
            if resp is not None and resp.status_code not in self.retryable_status:
                resp.raise_for_status()  # 4xx/other errors raise immediately
                return resp
            last_error = error if resp is None else f"HTTP {resp.status_code}"

            if attempt == s.max_retries:
                break
            delay = self._retry_delay(attempt, resp)
            logger.warning(
                "%s %s -> %s; retry %d in %.1fs",
                method,
                url,
                last_error,
                attempt + 1,
                delay,
            )
            await _wait_before_retry(delay, stop)

        raise RetriesExhaustedError(method, url, s.max_retries + 1, last_error)


async def _wait_before_retry(delay: float, stop: asyncio.Event | None) -> None:
    """Wait ``delay`` seconds; with a ``stop`` signal, return early once it's set.

    The retry loop's next check then sees the stop and abandons the request.
    """
    if stop is None:
        await asyncio.sleep(delay)
        return
    with contextlib.suppress(TimeoutError):  # timing out is the normal case
        await asyncio.wait_for(stop.wait(), timeout=delay)
