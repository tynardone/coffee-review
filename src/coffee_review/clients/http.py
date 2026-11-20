"""
Shared async http plumbing: concurrency cap, rate limit, retries with exponential
backoff and full jitter, and Retry-After handling.

Service clients subclass ``BaseHttpClient`` and call ``self.request(...)``.
Nothing outside of coffee_review.clients should import this module.
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING

import httpx
from aiolimiter import AsyncLimiter

if TYPE_CHECKING:
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


def _make_limiter(rate_per_sec: float) -> AsyncLimiter:
    """Build a limiter that also works for rates below 1/sec"""
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


class BaseHttpClient:
    """Async Http client with limits and retries. Use it as an async context manager::

    async with SomeClient(settings) as client:
        await client.some_method()
    """

    def __init__(
        self,
        settings: HttpClientSettings,
        *,
        headers: dict[str, str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self._semaphore = asyncio.Semaphore(settings.max_concurrency)
        self._limiter = _make_limiter(settings.rate_per_sec)
        self._headers = {"User-Agent": settings.user_agent, **(headers or {})}
        self._transport = transport  # lets tests pass httpx.MockTransport
        self._client: httpx.AsyncClient | None = None

    async def open(self) -> None:
        if self._client is None:
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
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _backoff_delay(self, attempt: int) -> float:
        """Exponential backoff with full jitter:
        uniform(0, min(max, base * 2**attempt)).
        """
        s = self.settings
        cap = min(s.backoff_max_seconds, s.backoff_base_seconds * 2**attempt)
        return random.uniform(0, cap)
