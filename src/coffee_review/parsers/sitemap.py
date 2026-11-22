"""Parse coffeereview.com sitemaps into review entries.

Pure functions: XML text in, models out. No network, no files.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from pydantic import ValidationError

from coffee_review.models.scrape import SitemapEntry

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = logging.getLogger(__name__)

NAMESPACE = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
REVIEW_SITEMAP_PATH = re.compile(r"^/review-sitemap\d*\.xml$")
REVIEW_PATH = re.compile(r"^/review/([^/]+)/$")


class SitemapParseError(Exception):
    """Raised when sitemap XML isn't well-formed."""


def _parse_xml(xml: str) -> ET.Element:
    try:
        return ET.fromstring(xml)
    except ET.ParseError as exc:
        raise SitemapParseError(f"sitemap is not well-formed XML: {exc}") from exc


def _text(element: ET.Element, tag: str) -> str | None:
    child = element.find(f"sm:{tag}", NAMESPACE)
    if child is None or child.text is None:
        return None
    return child.text.strip() or None


def _parse_lastmod(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        logger.debug("unparseable lastmod %r", value)
        return None
    if parsed.tzinfo is None:
        logger.debug("lastmod without timezone %r", value)
        return None
    return parsed


def parse_sitemap_index(xml: str) -> list[str]:
    """Return the URLs of the review sitemaps listed in the sitemap index."""
    root = _parse_xml(xml)
    urls = (_text(sitemap, "loc") for sitemap in root.findall("sm:sitemap", NAMESPACE))
    return [
        url
        for url in urls
        if url is not None and REVIEW_SITEMAP_PATH.match(urlsplit(url).path)
    ]


def parse_review_sitemap(xml: str) -> list[SitemapEntry]:
    """Return one entry per review URL in a review sitemap, skipping anything else."""
    root = _parse_xml(xml)
    entries: list[SitemapEntry] = []
    for url_element in root.findall("sm:url", NAMESPACE):
        url = _text(url_element, "loc")
        match = REVIEW_PATH.match(urlsplit(url).path) if url else None
        if url is None or match is None:
            logger.debug("skipping non-review sitemap entry %r", url)
            continue
        try:
            entry = SitemapEntry(
                slug=match.group(1),
                url=url,
                lastmod=_parse_lastmod(_text(url_element, "lastmod")),
            )
        except ValidationError:
            logger.debug("skipping entry with invalid slug %r", url)
            continue
        entries.append(entry)
    return entries


def dedupe_entries(entries: Iterable[SitemapEntry]) -> list[SitemapEntry]:
    """Keep one entry per slug: the one with the newest lastmod (None loses).

    Order follows each slug's first appearance.
    """
    kept: dict[str, SitemapEntry] = {}
    for entry in entries:
        current = kept.get(entry.slug)
        if current is None or _is_newer(entry.lastmod, current.lastmod):
            kept[entry.slug] = entry
    return list(kept.values())


def _is_newer(candidate: datetime | None, current: datetime | None) -> bool:
    if candidate is None:
        return False
    return current is None or candidate > current
