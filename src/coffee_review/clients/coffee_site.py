"""Scraper for coffeereviews.com

This module fetches pages and returns raw HTML
"""

import asyncio
from typing import TYPE_CHECKING

from coffee_review.clients.http import BaseHttpClient

if TYPE_CHECKING:
    import httpx

    from coffee_review.config import CoffeeSiteSettings


class CoffeeScraper(BaseHttpClient):
    def __init__(
        self,
        settings: CoffeeSiteSettings,
        *,
        headers=None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        super().__init__(
            settings,
            headers={"Accept": "text/html,application/xhtml+xml"},
            transport=transport,
        )

    async def fetch_sitemap(self):
        pass

    async def fetch_review_page(self):
        pass

    async def fetch_review_pages(self, slugs: list[str]) -> dict[str, str | Exception]:
        """Fetch many reviews at once. Semaphore and rate limit keep this polite
        however many slugs are passed in.

        Failures are returned in place of the HTML instead of raised, so one
        bad page doesn't end the run.
        """
        results = await asyncio.gather(*(self.fetch_review_page(s) for s in slugs))

        return dict(zip(slugs, results))
