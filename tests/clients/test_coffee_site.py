"""Tests for coffee_review.clients.coffee_site: how each response is classified."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from coffee_review.clients.coffee_site import (
    CoffeeScraper,
    Failed,
    Fetched,
    FetchResult,
    Gone,
    Pushback,
)
from coffee_review.config import CoffeeSiteSettings
from coffee_review.models import SitemapEntry

SITE = "https://www.coffeereview.com"
CHALLENGE_HTML = (
    Path(__file__).parent.parent / "fixtures" / "pages" / "cloudflare_challenge.html"
).read_text(encoding="utf-8")
ENTRY = SitemapEntry(
    slug="kenya-karinga-4", url=f"{SITE}/review/kenya-karinga-4/", lastmod=None
)


class FakeServer:
    """Answers each path from a list of responses, one per request."""

    def __init__(self, routes: dict[str, list[httpx.Response | Exception]]) -> None:
        self.routes = routes
        self.calls: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request.url.path)
        reply = self.routes[request.url.path].pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def scraper(server: FakeServer) -> CoffeeScraper:
    settings = CoffeeSiteSettings(
        rate_per_sec=1000, max_retries=2, backoff_base_seconds=0.01
    )
    return CoffeeScraper(settings, transport=httpx.MockTransport(server.handler))


async def fetch_page(
    *replies: httpx.Response | Exception,
) -> tuple[FetchResult, FakeServer]:
    server = FakeServer({"/review/kenya-karinga-4/": list(replies)})
    async with scraper(server) as client:
        result = await client.fetch_review_page(ENTRY)
    return result, server


@pytest.fixture(autouse=True)
def _no_real_sleeps(recorded_sleeps: list[float]) -> None:
    """Retries back off with asyncio.sleep; never actually wait in these tests."""


async def test_review_page_fetched() -> None:
    result, _ = await fetch_page(httpx.Response(200, text="<html>review</html>"))

    assert result == Fetched(text="<html>review</html>")


async def test_429_is_pushback_and_not_retried() -> None:
    result, server = await fetch_page(httpx.Response(429))

    assert isinstance(result, Pushback)
    assert result.reason == "HTTP 429"
    assert result.status == 429
    assert result.url == ENTRY.url
    assert len(server.calls) == 1


async def test_403_is_pushback() -> None:
    result, _ = await fetch_page(httpx.Response(403))

    assert isinstance(result, Pushback)


async def test_challenge_page_with_200_is_pushback() -> None:
    result, _ = await fetch_page(httpx.Response(200, text=CHALLENGE_HTML))

    assert isinstance(result, Pushback)
    assert result.reason == "Cloudflare challenge"


async def test_challenge_header_is_pushback() -> None:
    result, _ = await fetch_page(
        httpx.Response(200, text="<html/>", headers={"cf-mitigated": "challenge"})
    )

    assert isinstance(result, Pushback)


async def test_503_challenge_page_is_pushback() -> None:
    result, _ = await fetch_page(httpx.Response(503, text=CHALLENGE_HTML))

    assert isinstance(result, Pushback)


async def test_plain_503_is_failed_and_not_retried() -> None:
    result, server = await fetch_page(httpx.Response(503, text="overloaded"))

    assert result == Failed(error="HTTP 503")
    assert len(server.calls) == 1


@pytest.mark.parametrize("status", [404, 410])
async def test_404_is_gone(status: int) -> None:
    result, _ = await fetch_page(httpx.Response(status))

    assert result == Gone(reason=f"HTTP {status}")


async def test_redirect_to_homepage_is_gone() -> None:
    server = FakeServer(
        {
            "/review/kenya-karinga-4/": [
                httpx.Response(301, headers={"Location": f"{SITE}/"})
            ],
            "/": [httpx.Response(200, text="<html>home</html>")],
        }
    )
    async with scraper(server) as client:
        result = await client.fetch_review_page(ENTRY)

    assert result == Gone(reason="redirected to /")


async def test_redirect_to_other_slug_is_gone() -> None:
    server = FakeServer(
        {
            "/review/kenya-karinga-4/": [
                httpx.Response(
                    301, headers={"Location": f"{SITE}/review/kenya-karinga-5/"}
                )
            ],
            "/review/kenya-karinga-5/": [httpx.Response(200, text="<html/>")],
        }
    )
    async with scraper(server) as client:
        result = await client.fetch_review_page(ENTRY)

    assert result == Gone(reason="redirected to /review/kenya-karinga-5/")


async def test_500_retried_then_failed() -> None:
    result, server = await fetch_page(*(httpx.Response(500) for _ in range(3)))

    assert isinstance(result, Failed)
    assert "HTTP 500" in result.error
    assert len(server.calls) == 3


async def test_network_error_is_failed() -> None:
    result, _ = await fetch_page(*(httpx.ConnectError("refused") for _ in range(3)))

    assert isinstance(result, Failed)
    assert "ConnectError" in result.error


async def test_sitemap_fetched() -> None:
    server = FakeServer({"/sitemap_index.xml": [httpx.Response(200, text="<xml/>")]})
    async with scraper(server) as client:
        result = await client.fetch_sitemap(f"{SITE}/sitemap_index.xml")

    assert result == Fetched(text="<xml/>")


async def test_sitemap_429_is_pushback() -> None:
    server = FakeServer({"/sitemap_index.xml": [httpx.Response(429)]})
    async with scraper(server) as client:
        result = await client.fetch_sitemap(f"{SITE}/sitemap_index.xml")

    assert isinstance(result, Pushback)
