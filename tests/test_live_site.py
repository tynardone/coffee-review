"""Checks against the real coffeereview.com. Skipped unless asked for.

Run with ``uv run pytest -m network``. One request per run; never in CI.
"""

from __future__ import annotations

import pytest

from coffee_review.clients.coffee_site import CoffeeScraper, Fetched
from coffee_review.config import CoffeeSiteSettings
from coffee_review.parsers.sitemap import parse_sitemap_index


@pytest.mark.network
async def test_live_sitemap_lists_review_sitemaps() -> None:
    site = CoffeeSiteSettings()
    async with CoffeeScraper(site) as client:
        result = await client.fetch_sitemap(str(site.sitemap_url))

    assert isinstance(result, Fetched)
    assert len(parse_sitemap_index(result.text)) >= 1
