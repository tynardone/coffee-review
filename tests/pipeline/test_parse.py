"""Tests for coffee_review.pipeline.parse, against the in-memory fakes.

The raw store is seeded with fixture pages, the same way a scrape fills it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from coffee_review.models.scrape import SitemapEntry
from coffee_review.pipeline import parse as parse_module
from coffee_review.pipeline.parse import run_parse
from tests.fakes import InMemoryParsedStore, InMemoryRawStore

if TYPE_CHECKING:
    from coffee_review.models.parsed import ParsedReview

FIXTURES = Path(__file__).parent.parent / "fixtures" / "pages" / "review"
T = datetime(2026, 11, 1, 12, tzinfo=UTC)


def entry(slug: str) -> SitemapEntry:
    return SitemapEntry(
        slug=slug, url=f"https://www.coffeereview.com/review/{slug}/", lastmod=T
    )


def fixture_html(name: str) -> str:
    return (FIXTURES / f"{name}.html").read_text(encoding="utf-8")


def raw_with(**pages: str) -> InMemoryRawStore:
    """A raw store holding one saved page per slug=fixture_name."""
    raw = InMemoryRawStore()
    for slug, name in pages.items():
        raw.save_page(entry(slug), fixture_html(name), T, lookback=False)
    return raw


class RecordingProgress:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def start(self, total: int) -> None:
        self.calls.append(("start", total))

    def advance(self, outcome: str) -> None:
        self.calls.append(("advance", outcome))

    def finish(self) -> None:
        self.calls.append(("finish", None))


def slugs(records: list[ParsedReview]) -> list[str]:
    return [r.slug for r in records]


def test_parses_every_saved_page() -> None:
    raw = raw_with(a="modern_roasted", b="green_new")
    parsed = InMemoryParsedStore()

    result = run_parse(raw=raw, parsed=parsed)

    assert slugs(parsed.read_reviews("roasted")) == ["a"]
    assert slugs(parsed.read_reviews("green")) == ["b"]
    assert result.report.by_dataset == {"roasted": 1, "green": 1}


def test_rows_without_saved_copy_are_skipped() -> None:
    raw = raw_with(a="modern_roasted")
    raw.record_failure(entry("never-saved"), "HTTP 500", T)

    result = run_parse(raw=raw, parsed=InMemoryParsedStore())

    assert result.report.skipped == 1
    assert result.report.pages == 2


def test_delisted_flag_from_manifest() -> None:
    raw = raw_with(a="modern_roasted", b="older_roasted")
    raw.mark_delisted(["b"], T)
    parsed = InMemoryParsedStore()

    run_parse(raw=raw, parsed=parsed)

    delisted = {r.slug: r.delisted for r in parsed.read_reviews("roasted")}
    assert delisted == {"a": False, "b": True}


def test_failed_status_with_saved_copy_is_still_parsed() -> None:
    raw = raw_with(a="modern_roasted")
    raw.record_failure(entry("a"), "HTTP 500", T)  # a later re-fetch failed
    parsed = InMemoryParsedStore()

    run_parse(raw=raw, parsed=parsed)

    assert slugs(parsed.read_reviews("roasted")) == ["a"]


def test_missing_page_file_is_an_issue() -> None:
    raw = raw_with(a="modern_roasted", b="green_new")
    del raw.pages["b"]  # the manifest says it was saved, but the page is gone
    parsed = InMemoryParsedStore()

    run_parse(raw=raw, parsed=parsed)

    assert [(i.slug, i.kind) for i in parsed.read_issues()] == [("b", "page_missing")]
    assert slugs(parsed.read_reviews("roasted")) == ["a"]


@pytest.mark.parametrize(
    "error",
    [
        EOFError("Compressed file ended before the end-of-stream marker"),
        UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"),
        ValueError("not a valid slug: 'B'"),
    ],
)
def test_unreadable_page_is_an_issue(
    error: Exception, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = raw_with(a="modern_roasted", b="green_new")
    read_page = raw.read_page

    def corrupt_b(slug: str) -> str:
        if slug == "b":
            raise error
        return read_page(slug)

    monkeypatch.setattr(raw, "read_page", corrupt_b)
    parsed = InMemoryParsedStore()

    run_parse(raw=raw, parsed=parsed)

    issues = parsed.read_issues()
    assert [(i.slug, i.kind) for i in issues] == [("b", "page_missing")]
    assert issues[0].detail.startswith(f"{type(error).__name__}: ")
    assert slugs(parsed.read_reviews("roasted")) == ["a"]


def test_bad_page_does_not_stop_the_run() -> None:
    raw = raw_with(a="no_template", b="modern_roasted")
    parsed = InMemoryParsedStore()

    run_parse(raw=raw, parsed=parsed)

    assert slugs(parsed.read_reviews("roasted")) == ["b"]
    assert [(i.slug, i.kind) for i in parsed.read_issues()] == [
        ("a", "no_review_template")
    ]


def test_unexpected_parser_error_is_an_issue(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = raw_with(a="modern_roasted", b="green_new")

    def broken_parser(slug: str, *args: object, **kwargs: object) -> object:
        raise RuntimeError("selectolax fell over")

    monkeypatch.setattr(parse_module, "parse_review_page", broken_parser)
    parsed = InMemoryParsedStore()

    run_parse(raw=raw, parsed=parsed)

    issues = parsed.read_issues()
    assert [(i.slug, i.kind) for i in issues] == [
        ("a", "parse_error"),
        ("b", "parse_error"),
    ]
    assert issues[0].detail == "RuntimeError: selectolax fell over"


def test_report_written_with_markdown() -> None:
    parsed = InMemoryParsedStore()

    result = run_parse(raw=raw_with(a="modern_roasted"), parsed=parsed)

    assert parsed.report_markdown.startswith("# Parse report")
    assert result.report_location == "in memory"


def test_progress_reports_each_page() -> None:
    raw = raw_with(a="modern_roasted", b="green_new", c="no_template")
    progress = RecordingProgress()

    run_parse(raw=raw, parsed=InMemoryParsedStore(), progress=progress)

    assert progress.calls == [
        ("start", 3),
        ("advance", "roasted"),
        ("advance", "green"),
        ("advance", "issue"),
        ("finish", None),
    ]


def test_elapsed_from_clock() -> None:
    ticks = iter([10.0, 14.5])

    result = run_parse(
        raw=raw_with(a="modern_roasted"),
        parsed=InMemoryParsedStore(),
        clock=lambda: next(ticks),
    )

    assert result.elapsed_seconds == 4.5


def test_summary_logged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO", logger="coffee_review")

    run_parse(raw=raw_with(a="modern_roasted"), parsed=InMemoryParsedStore())

    assert "parse finished: 1 pages · 1 roasted" in caplog.text
