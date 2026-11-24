"""The parse stage: raw store → page parser → report → parsed store.

``run_parse`` does the work with both stores passed in, so tests can hand it
fakes. ``parse`` is the composition root that opens the real ones. Like the
scrape stage, this module never sees how anything is stored.

One bad page never stops a run: anything that goes wrong with a page becomes
a ``ParseIssue`` in the report, and the run carries on.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from coffee_review.models.parsed import ParsedPage, ParseIssue
from coffee_review.parsers.review_page import parse_review_page
from coffee_review.pipeline.parse_report import (
    build_parse_report,
    render_report_markdown,
)
from coffee_review.pipeline.progress import NoProgress
from coffee_review.storage.parsed_store import open_parsed_store
from coffee_review.storage.raw_store import PageNotFoundError, open_raw_store

if TYPE_CHECKING:
    from collections.abc import Callable

    from coffee_review.config import Settings
    from coffee_review.models.parsed import ParsedReview
    from coffee_review.models.scrape import ManifestRecord
    from coffee_review.pipeline.parse_report import ParseReport
    from coffee_review.pipeline.progress import RunProgress
    from coffee_review.storage.parsed_store import ParsedStore
    from coffee_review.storage.raw_store import RawStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ParseRunResult:
    """What a parse run produced.

    Attributes:
        report: The parse report's contents.
        elapsed_seconds: How long the run took.
        report_location: Where the Markdown report was written.
    """

    report: ParseReport
    elapsed_seconds: float
    report_location: str


def parse(settings: Settings, *, progress: RunProgress | None = None) -> ParseRunResult:
    """Parse every saved page with the real stores.

    Args:
        settings: The project settings.
        progress: Where to report progress; None for none.

    Returns:
        The report, how long the run took, and where the report went.
    """
    raw = open_raw_store(settings)
    try:
        parsed = open_parsed_store(settings)
        try:
            return run_parse(raw=raw, parsed=parsed, progress=progress)
        finally:
            parsed.close()
    finally:
        raw.close()


def run_parse(
    *,
    raw: RawStore,
    parsed: ParsedStore,
    progress: RunProgress | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> ParseRunResult:
    """Parse every saved page, and write both datasets, the issues and the report.

    Args:
        raw: Where to read saved pages and the manifest.
        parsed: Where to write the parsed layer.
        progress: Where to report progress; None for none.
        clock: Seconds counter for timing the run (tests pass a fake).

    Returns:
        The report, how long the run took, and where the report went.
    """
    progress = progress or NoProgress()
    started = clock()
    manifest = raw.load_manifest()
    rows = sorted(
        (row for row in manifest.values() if row.fetched_at is not None),
        key=lambda row: row.slug,
    )
    records: list[ParsedReview] = []
    issues: list[ParseIssue] = []

    progress.start(len(rows))
    try:
        for row in rows:
            page = _parse_one(raw, row)
            issues.extend(page.issues)
            if page.record is None:
                progress.advance("issue")
            else:
                records.append(page.record)
                progress.advance(page.record.dataset)
    finally:
        progress.finish()

    report = build_parse_report(
        records, issues, pages=len(manifest), skipped=len(manifest) - len(rows)
    )
    parsed.write(records, issues, render_report_markdown(report, issues))
    elapsed = clock() - started
    logger.info(report.summary_line(elapsed))
    return ParseRunResult(
        report=report,
        elapsed_seconds=elapsed,
        report_location=parsed.report_location(),
    )


def _parse_one(raw: RawStore, row: ManifestRecord) -> ParsedPage:
    """One manifest row's page, parsed; any failure becomes an issue."""
    try:
        html = raw.read_page(row.slug)
    except PageNotFoundError:
        return _page_missing(row.slug, "no saved copy")
    except Exception as exc:  # truncated gzip, bad bytes, bad slug in the manifest
        logger.exception("could not read the saved copy of %s", row.slug)
        return _page_missing(row.slug, f"{type(exc).__name__}: {exc}")
    try:
        return parse_review_page(
            row.slug, row.url, html, delisted=row.delisted_at is not None
        )
    except Exception as exc:  # a parser bug must not stop the other pages
        logger.exception("parser failed on %s", row.slug)
        issue = ParseIssue(
            slug=row.slug, kind="parse_error", detail=f"{type(exc).__name__}: {exc}"
        )
        return ParsedPage(record=None, issues=[issue])


def _page_missing(slug: str, detail: str) -> ParsedPage:
    issue = ParseIssue(slug=slug, kind="page_missing", detail=detail)
    return ParsedPage(record=None, issues=[issue])
