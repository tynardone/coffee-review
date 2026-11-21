"""Scraper for coffeereview.com.

This module fetches pages and returns raw HTML. Parsing lives in
``coffee_review.parsers``.
"""

from __future__ import annotations

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
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(
            settings,
            headers={"Accept": "text/html,application/xhtml+xml"},
            transport=transport,
        )

    async def fetch_sitemap(self) -> str:
        raise NotImplementedError  # built in M1

    async def fetch_review_page(self, slug: str) -> str:
        raise NotImplementedError  # built in M1

    async def fetch_review_pages(self, slugs: list[str]) -> dict[str, str | Exception]:
        """Fetch many reviews at once. Semaphore and rate limit keep this polite
        however many slugs are passed in.

        Failures are returned in place of the HTML instead of raised, so one
        bad page doesn't end the run.
        """
        # TODO(M1): gather without return_exceptions=True raises on the first
        # failure, which contradicts the docstring. Fix when the scraper is built.
        results = await asyncio.gather(*(self.fetch_review_page(s) for s in slugs))

        return dict(zip(slugs, results, strict=True))
