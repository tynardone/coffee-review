"""Client for coffeereview.com: fetches sitemaps and review pages.

Every fetch returns one of four results instead of raising, so the pipeline
can decide what happens next:

- ``Fetched``: the page, ready to save.
- ``Failed``: a temporary failure (network, 5xx); try again next run.
- ``Gone``: the page isn't there any more (404, redirected elsewhere).
- ``Pushback``: the site is telling us to stop (429, 403, a challenge page).

Parsing lives in ``coffee_review.parsers``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlsplit

import httpx

from coffee_review.clients.http import (
    BaseHttpClient,
    RequestAbandonedError,
    RetriesExhaustedError,
)

if TYPE_CHECKING:
    import asyncio

    from coffee_review.config import CoffeeSiteSettings
    from coffee_review.models.scrape import SitemapEntry

# Markers of a Cloudflare challenge ("checking your browser") page.
CHALLENGE_MARKERS = ("challenge-platform", "cf-chl-", "<title>Just a moment...</title>")
PUSHBACK_STATUS = frozenset({403, 429})
GONE_STATUS = frozenset({404, 410})


@dataclass(frozen=True)
class Fetched:
    """The page was fetched; ``text`` is its HTML."""

    text: str


@dataclass(frozen=True)
class Failed:
    """The fetch failed for now; ``error`` says how. Tried again next run."""

    error: str


@dataclass(frozen=True)
class Gone:
    """The page no longer exists (404, or redirected away); ``reason`` says why."""

    reason: str


@dataclass(frozen=True)
class Pushback:
    """The site is pushing back (a 429, 403 or challenge page): stop the run.

    Attributes:
        reason: What happened, for the summary line.
        url: The address that got the pushback.
        status: The HTTP status, when there was one.
    """

    reason: str
    url: str
    status: int | None


type FetchResult = Fetched | Failed | Gone | Pushback


def is_challenge(response: httpx.Response) -> bool:
    """True if the response is a Cloudflare challenge page, whatever its status."""
    if response.headers.get("cf-mitigated") == "challenge":
        return True
    return any(marker in response.text for marker in CHALLENGE_MARKERS)


class CoffeeScraper(BaseHttpClient):
    """Fetches coffeereview.com's sitemaps and review pages.

    Every fetch comes back as one of ``Fetched``, ``Failed``, ``Gone`` or
    ``Pushback``; it never raises for a bad response.

    Args:
        settings: The site's address, speed and retry settings.
        transport: An httpx transport to use instead of the network (tests).
    """

    # 429 and 503 are not retried here: they come back to the pipeline, which
    # decides whether the site is pushing back.
    retryable_status = frozenset({408, 500, 502, 504})

    def __init__(
        self,
        settings: CoffeeSiteSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(
            settings,
            headers={"Accept": "text/html,application/xhtml+xml"},
            transport=transport,
        )

    async def fetch_sitemap(self, url: str) -> FetchResult:
        """Fetch a sitemap (index or child) as text."""
        return await self._fetch(url, expected_path=None, stop=None)

    async def fetch_review_page(
        self, entry: SitemapEntry, *, stop: asyncio.Event | None = None
    ) -> FetchResult:
        """Fetch one review page.

        A redirect to any other page counts as ``Gone``.

        Args:
            entry: The review, as the sitemap lists it.
            stop: When set while this page is being retried, no further retry is
                sent and the page comes back ``Failed``, to be fetched next run.

        Returns:
            ``Fetched``, ``Failed``, ``Gone`` or ``Pushback``; never raises for a bad
            response.
        """
        # Compare decoded paths: the sitemap lists "ka%ca%bbu-..." but the site
        # answers at "kaʻu-..." (httpx reports response paths decoded). Same page.
        expected_path = unquote(urlsplit(entry.url).path)
        return await self._fetch(entry.url, expected_path=expected_path, stop=stop)

    async def _fetch(
        self, url: str, *, expected_path: str | None, stop: asyncio.Event | None
    ) -> FetchResult:
        try:
            response = await self.request("GET", url, stop=stop)
        except RetriesExhaustedError as exc:
            return Failed(error=str(exc))
        except RequestAbandonedError as exc:
            return Failed(error=f"stopped before retrying: {exc.last_error}")
        except httpx.HTTPStatusError as exc:
            return self._classify_error(exc.response, url)

        if is_challenge(response):
            return Pushback(reason="Cloudflare challenge", url=url, status=200)
        if expected_path is not None and response.url.path != expected_path:
            return Gone(reason=f"redirected to {response.url.path}")
        return Fetched(text=response.text)

    @staticmethod
    def _classify_error(response: httpx.Response, url: str) -> FetchResult:
        status = response.status_code
        if is_challenge(response):
            return Pushback(reason="Cloudflare challenge", url=url, status=status)
        if status in PUSHBACK_STATUS:
            return Pushback(reason=f"HTTP {status}", url=url, status=status)
        if status in GONE_STATUS:
            return Gone(reason=f"HTTP {status}")
        return Failed(error=f"HTTP {status}")
