"""End-to-end tests for the scrape pipeline, against a fake site and a fake store.

No network and no files: FakeSite answers the HTTP requests and
InMemoryRawStore (held to the real store's contract) records the results.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from coffee_review.clients.coffee_site import CoffeeScraper, FetchResult
from coffee_review.config import Settings
from coffee_review.models.scrape import FetchReason, SitemapEntry
from coffee_review.pipeline import scrape as scrape_module
from coffee_review.pipeline.progress import NoProgress
from coffee_review.pipeline.scrape import run_scrape
from coffee_review.storage.raw_store import ManifestSchemaError
from tests.fakes import FakeSite, InMemoryRawStore, Reply, unthrottled_site_settings

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Callable

    from coffee_review.models.scrape import ScrapeSummary
    from coffee_review.pipeline.progress import ScrapeOutcome, ScrapeProgress

SITE = "https://www.coffeereview.com"
T = datetime(2026, 11, 1, 12, tzinfo=UTC)
# Reviews were last edited well before the first run, so the 24-hour lookback
# rule never fires by accident in these tests.
OLD = T - timedelta(days=2)
FIRST_RUN = T + timedelta(days=1)
LATER_RUN = T + timedelta(days=2)


@pytest.fixture(autouse=True)
def _no_real_sleeps(recorded_sleeps: list[float]) -> None:
    """Retries back off with asyncio.sleep; never actually wait in these tests."""


def settings() -> Settings:
    # One worker makes the fetch order deterministic; no retries keeps
    # failure tests to one request per page.
    site = unthrottled_site_settings(max_concurrency=1, max_retries=0)
    return Settings(_env_file=None).model_copy(update={"coffee_site": site})


def index_xml(*sitemaps: str) -> str:
    body = "".join(f"<sitemap><loc>{SITE}{path}</loc></sitemap>" for path in sitemaps)
    return (
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{body}</sitemapindex>"
    )


def review_sitemap_xml(lastmods: dict[str, datetime]) -> str:
    body = "".join(
        f"<url><loc>{SITE}/review/{slug}/</loc>"
        f"<lastmod>{lastmod.isoformat()}</lastmod></url>"
        for slug, lastmod in lastmods.items()
    )
    return (
        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>'
    )


def site_with(
    lastmods: dict[str, datetime], pages: dict[str, Reply] | None = None
) -> FakeSite:
    """A site whose one review sitemap lists ``lastmods``; pages default to 200."""
    routes: dict[str, Reply] = {
        "/sitemap_index.xml": httpx.Response(
            200, text=index_xml("/review-sitemap.xml")
        ),
        "/review-sitemap.xml": httpx.Response(200, text=review_sitemap_xml(lastmods)),
    }
    for slug in lastmods:
        routes[f"/review/{slug}/"] = httpx.Response(200, text=f"<html>{slug}</html>")
    for slug, reply in (pages or {}).items():
        routes[f"/review/{slug}/"] = reply
    return FakeSite(routes)


THREE = {"a": OLD, "b": OLD - timedelta(hours=1), "c": OLD - timedelta(hours=2)}


async def scrape(
    site: FakeSite,
    store: InMemoryRawStore,
    *,
    now: datetime = FIRST_RUN,
    full: bool = False,
    limit: int | None = None,
    allow_delisting: bool = False,
    clock: Callable[[], float] = time.monotonic,
    progress: ScrapeProgress | None = None,
) -> ScrapeSummary:
    config = settings()
    async with CoffeeScraper(config.coffee_site, transport=site.transport) as client:
        return await run_scrape(
            config,
            client=client,
            store=store,
            full=full,
            limit=limit,
            now=now,
            allow_delisting=allow_delisting,
            clock=clock,
            progress=progress or NoProgress(),
        )


def page_requests(site: FakeSite) -> list[str]:
    return [path for path in site.requested if path.startswith("/review/")]


def seed(store: InMemoryRawStore, slugs: list[str]) -> None:
    for slug in slugs:
        entry = SitemapEntry(slug=slug, url=f"{SITE}/review/{slug}/", lastmod=OLD)
        store.save_page(entry, "<html/>", FIRST_RUN, lookback=False)


async def test_first_run_fetches_everything() -> None:
    store = InMemoryRawStore()

    summary = await scrape(site_with(THREE), store)

    assert summary.outcome == "finished"
    assert summary.fetched == {FetchReason.NEW: 3}
    assert set(store.pages) == {"a", "b", "c"}
    assert store.read_page("a") == "<html>a</html>"


async def test_rerun_fetches_no_pages() -> None:
    store = InMemoryRawStore()
    await scrape(site_with(THREE), store)
    site = site_with(THREE)

    summary = await scrape(site, store, now=LATER_RUN)

    assert page_requests(site) == []
    assert summary.unchanged == 3
    assert summary.describe().startswith("scrape finished: 0 fetched, 3 unchanged")


async def test_changed_review_refetched() -> None:
    store = InMemoryRawStore()
    await scrape(site_with(THREE), store)
    site = site_with({**THREE, "b": FIRST_RUN + timedelta(hours=1)})

    summary = await scrape(site, store, now=LATER_RUN)

    assert page_requests(site) == ["/review/b/"]
    assert summary.fetched == {FetchReason.CHANGED: 1}


async def test_pushback_stops_and_next_run_resumes() -> None:
    store = InMemoryRawStore()
    first = site_with(THREE, pages={"b": httpx.Response(429)})

    stopped = await scrape(first, store)

    assert stopped.outcome == "stopped"
    assert stopped.reason == f"HTTP 429 at {SITE}/review/b/"
    assert stopped.remaining == 1
    assert page_requests(first) == ["/review/a/", "/review/b/"]
    assert set(store.pages) == {"a"}

    second = site_with(THREE)
    resumed = await scrape(second, store, now=LATER_RUN)

    assert resumed.outcome == "finished"
    assert page_requests(second) == ["/review/b/", "/review/c/"]
    assert set(store.pages) == {"a", "b", "c"}


async def test_five_failures_in_a_row_stop() -> None:
    lastmods = {f"r{i}": OLD - timedelta(hours=i) for i in range(6)}
    pages: dict[str, Reply] = {slug: httpx.Response(500) for slug in lastmods}
    store = InMemoryRawStore()

    summary = await scrape(site_with(lastmods, pages), store)

    assert summary.outcome == "stopped"
    assert summary.failed == 5
    assert summary.remaining == 1
    assert summary.reason is not None
    assert "5 failures in a row" in summary.reason


async def test_success_resets_failure_streak() -> None:
    lastmods = {f"r{i}": OLD - timedelta(hours=i) for i in range(9)}
    pages: dict[str, Reply] = {
        slug: httpx.Response(500) for slug in lastmods if slug != "r4"
    }
    store = InMemoryRawStore()

    summary = await scrape(site_with(lastmods, pages), store)

    assert summary.outcome == "finished"
    assert summary.failed == 8


async def test_gone_pages_do_not_stop_and_are_not_refetched() -> None:
    lastmods = {f"r{i}": OLD - timedelta(hours=i) for i in range(6)}
    pages: dict[str, Reply] = {slug: httpx.Response(404) for slug in lastmods}
    store = InMemoryRawStore()

    summary = await scrape(site_with(lastmods, pages), store)
    rerun_site = site_with(lastmods, pages)
    await scrape(rerun_site, store, now=LATER_RUN)

    assert summary.outcome == "finished"
    assert sorted(summary.gone_slugs) == sorted(lastmods)
    assert page_requests(rerun_site) == []


async def test_failed_pages_retried_next_run() -> None:
    store = InMemoryRawStore()
    await scrape(site_with(THREE, pages={"c": httpx.Response(500)}), store)
    site = site_with(THREE)

    summary = await scrape(site, store, now=LATER_RUN)

    assert page_requests(site) == ["/review/c/"]
    assert summary.fetched == {FetchReason.FAILED: 1}


async def test_sitemap_failure_marks_nothing_delisted() -> None:
    store = InMemoryRawStore()
    seed(store, ["old"])
    site = site_with(THREE)
    site.routes["/review-sitemap.xml"] = httpx.Response(500)

    summary = await scrape(site, store)

    assert summary.outcome == "error"
    assert store.records["old"].delisted_at is None
    assert page_requests(site) == []


async def test_sitemap_pushback_stops() -> None:
    site = site_with(THREE)
    site.routes["/sitemap_index.xml"] = httpx.Response(429)

    summary = await scrape(site, InMemoryRawStore())

    assert summary.outcome == "stopped"


async def test_index_without_review_sitemaps_is_error() -> None:
    site = site_with(THREE)
    site.routes["/sitemap_index.xml"] = httpx.Response(
        200, text=index_xml("/post-sitemap.xml")
    )
    store = InMemoryRawStore()
    seed(store, ["a"])

    summary = await scrape(site, store)

    assert summary.outcome == "error"
    assert store.records["a"].delisted_at is None


async def test_delisted_review_marked_and_relisted() -> None:
    listed = {f"r{i}": OLD for i in range(30)}
    store = InMemoryRawStore()
    seed(store, [*listed, "old"])

    summary = await scrape(site_with(listed), store, now=LATER_RUN)
    assert summary.delisted == 1
    assert store.records["old"].delisted_at == LATER_RUN

    relisted = await scrape(site_with({**listed, "old": OLD}), store, now=LATER_RUN)
    assert relisted.relisted == 1
    assert store.records["old"].delisted_at is None


async def test_delisting_guard_blocks_mass_delisting() -> None:
    store = InMemoryRawStore()
    seed(store, [f"r{i}" for i in range(100)])
    site = site_with({f"r{i}": OLD for i in range(60)})

    summary = await scrape(site, store)

    assert summary.outcome == "error"
    assert summary.reason is not None
    assert "40 of 100" in summary.reason
    assert "--allow-delisting" in summary.reason
    assert all(record.delisted_at is None for record in store.records.values())
    assert page_requests(site) == []


async def test_limit_caps_fetches() -> None:
    store = InMemoryRawStore()

    summary = await scrape(site_with(THREE), store, limit=2)

    assert set(store.pages) == {"a", "b"}
    assert summary.remaining == 1
    assert summary.outcome == "finished"


async def test_full_refetches_everything() -> None:
    store = InMemoryRawStore()
    await scrape(site_with(THREE), store)

    summary = await scrape(site_with(THREE), store, now=LATER_RUN, full=True)

    assert summary.fetched == {FetchReason.FULL: 3}


async def test_summary_counts_sitemap_and_page_requests() -> None:
    summary = await scrape(site_with(THREE), InMemoryRawStore())

    assert summary.measurement is not None
    assert summary.measurement.requests == 5  # index + 1 review sitemap + 3 pages


async def test_summary_elapsed_from_clock() -> None:
    ticks = iter([100.0, 130.0])

    summary = await scrape(
        site_with(THREE), InMemoryRawStore(), clock=lambda: next(ticks)
    )

    assert summary.measurement is not None
    assert summary.measurement.elapsed_seconds == 30.0
    assert summary.measurement.rate_per_sec == 1000
    assert summary.measurement.max_concurrency == 1


async def test_early_ending_is_measured_too() -> None:
    site = site_with(THREE)
    site.routes["/sitemap_index.xml"] = httpx.Response(429)

    summary = await scrape(site, InMemoryRawStore())

    assert summary.measurement is not None
    assert summary.measurement.requests == 1


class RecordingScraper(CoffeeScraper):
    """The real client, also noting the stop signal each page fetch was given."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.stops: list[asyncio.Event | None] = []

    async def fetch_review_page(
        self, entry: SitemapEntry, *, stop: asyncio.Event | None = None
    ) -> FetchResult:
        self.stops.append(stop)
        return await super().fetch_review_page(entry, stop=stop)


async def test_pushback_signals_the_client_to_stop_retrying() -> None:
    config = settings()
    site = site_with(THREE, pages={"b": httpx.Response(429)})
    async with RecordingScraper(config.coffee_site, transport=site.transport) as client:
        await run_scrape(
            config,
            client=client,
            store=InMemoryRawStore(),
            full=False,
            limit=None,
            now=FIRST_RUN,
        )

    signal = client.stops[0]
    assert signal is not None
    assert all(stop is signal for stop in client.stops)
    assert signal.is_set()


async def test_few_delistings_from_small_manifest_are_marked() -> None:
    # The real manifest after the first --limit runs: 40 rows.
    store = InMemoryRawStore()
    seed(store, [f"r{i}" for i in range(40)])

    summary = await scrape(site_with({f"r{i}": OLD for i in range(37)}), store)

    assert summary.outcome == "finished"
    assert summary.delisted == 3


async def test_allow_delisting_overrides_guard() -> None:
    store = InMemoryRawStore()
    seed(store, [f"r{i}" for i in range(100)])
    site = site_with({f"r{i}": OLD for i in range(60)})

    summary = await scrape(site, store, allow_delisting=True)

    assert summary.outcome == "finished"
    assert summary.delisted == 40


class FullDiskStore(InMemoryRawStore):
    """A store whose disk fills up on the first page save."""

    def save_page(self, *args: Any, **kwargs: Any) -> None:
        raise OSError("No space left on device")


async def test_worker_error_ends_in_a_measured_error_summary(
    caplog: pytest.LogCaptureFixture,
) -> None:
    summary = await scrape(site_with(THREE), FullDiskStore())

    assert summary.outcome == "error"
    assert summary.reason == "OSError: No space left on device"
    assert summary.measurement is not None
    assert "Traceback" in caplog.text  # the full error is in the log


def test_unreadable_manifest_ends_in_an_error_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def future_manifest(settings: Settings) -> InMemoryRawStore:
        raise ManifestSchemaError("manifest schema version 99; this code reads 1")

    monkeypatch.setattr(scrape_module, "open_raw_store", future_manifest)

    summary = scrape_module.scrape(settings(), full=False, limit=None, now=FIRST_RUN)

    assert summary.outcome == "error"
    assert summary.reason is not None
    assert "schema version 99" in summary.reason


class RecordingProgress:
    """A ScrapeProgress that writes down every call, for checking the pipeline."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def start(self, total: int) -> None:
        self.calls.append(("start", total))

    def advance(self, outcome: ScrapeOutcome) -> None:
        self.calls.append(("advance", outcome))

    def finish(self) -> None:
        self.calls.append(("finish", None))


async def test_progress_starts_with_the_planned_total() -> None:
    progress = RecordingProgress()

    await scrape(site_with(THREE), InMemoryRawStore(), limit=2, progress=progress)

    assert progress.calls[0] == ("start", 2)


async def test_progress_advances_once_per_recorded_page() -> None:
    progress = RecordingProgress()
    pages: dict[str, Reply] = {"b": httpx.Response(500), "c": httpx.Response(404)}

    await scrape(site_with(THREE, pages), InMemoryRawStore(), progress=progress)

    assert progress.calls == [
        ("start", 3),
        ("advance", "saved"),
        ("advance", "failed"),
        ("advance", "gone"),
        ("finish", None),
    ]


async def test_pushback_does_not_advance_progress() -> None:
    progress = RecordingProgress()
    site = site_with(THREE, pages={"b": httpx.Response(429)})

    await scrape(site, InMemoryRawStore(), progress=progress)

    assert progress.calls == [("start", 3), ("advance", "saved"), ("finish", None)]


async def test_progress_finishes_even_on_error() -> None:
    progress = RecordingProgress()

    await scrape(site_with(THREE), FullDiskStore(), progress=progress)

    assert progress.calls[-1] == ("finish", None)


async def test_progress_finishes_when_sitemaps_fail() -> None:
    progress = RecordingProgress()
    site = site_with(THREE)
    site.routes["/sitemap_index.xml"] = httpx.Response(429)

    await scrape(site, InMemoryRawStore(), progress=progress)

    assert progress.calls == [("finish", None)]
