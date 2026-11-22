# M1 Scraper: Design

Date: 2026-11-21
Status: draft, awaiting Tyler's approval (builder check done)
Parent: [pipeline roadmap design](2026-11-20-pipeline-roadmap-design.md), milestone M1.

## Intent

Download every review on coffeereview.com as raw HTML, keep a record of what was
fetched and when, and on later runs fetch only what's new or changed. This is the
first stage of the pipeline and the layer everything else is rebuilt from, so it
has to be **correct, resumable and polite** above all else. It is also the first
real demonstration of the project's structure: a pure decision function, a store,
a client and a pipeline module, each tested on its own.

## Goals

- `coffee-review scrape` fetches new and changed reviews; `--full` fetches all.
- A run that is interrupted or stopped resumes by simply running again; no work
  is lost and nothing is fetched twice without reason.
- The site is never pushed: any sign of pushback stops the run cleanly.
- Every run leaves a log and a one-line summary.
- Reviews that disappear from the sitemap are kept and marked, not deleted.

## Non-goals

- Parsing review content (M2). The scraper never looks inside a review page
  beyond detecting a challenge page.
- Scraping anything except reviews (posts, pages, cities, authors are skipped).
- Scheduling runs; Tyler runs the command.
- Keeping old versions of a page (decided in the roadmap: latest only).

## Anti-goals

- Saving a Cloudflare challenge page, an error page or a redirect target as if
  it were a review.
- Marking reviews delisted because a sitemap failed to download.
- Sending more requests after the site has said no.
- A partially written page or manifest after a crash.

## Constraints

- Speed: 5 requests/second by default, with concurrency worked out from the
  rate (see *Speed controls* below). About 31 minutes for a full pass, in
  line with Tyler's experience of the site from v1. Hard ceiling 10/second.
  ADR 0005.
- robots.txt (checked 2026-11-21) allows `/review/`, sets no crawl delay, and
  blocks named AI crawlers. Our User-Agent is the M0 placeholder until the
  public repo exists.
- Sitemap index `https://www.coffeereview.com/sitemap_index.xml` (Yoast) lists
  `review-sitemap.xml` and `review-sitemap2.xml` … `review-sitemap10.xml` (about
  1,000 entries each; ~9,400 total) among other sitemaps.
- Each review entry: `<loc>https://www.coffeereview.com/review/<slug>/</loc>`
  and `<lastmod>` in ISO 8601 with offset. Re-reviews get numbered slugs
  (`kenya-karinga-4`); every slug is its own review.
- Cloudflare is in front of the site.
- No new runtime dependencies: SQLite and XML parsing come from the standard
  library.

## Decisions made with Tyler

### Pushback stops the run

Pushback is any of:

- an HTTP 429, at any time;
- an HTTP 403, or any response recognized as a Cloudflare challenge page,
  whatever its status;
- 5 temporary failures in a row (a success resets the count; `gone` pages
  don't count, see below).

*My call:* a plain 503 is a failure, not pushback, unless it's a challenge
page. One 503 is usually a hiccup; several in a row trip the 5-in-a-row stop.

On pushback the run sends no new requests, lets in-flight requests finish and
saves their results, logs the cause (reason, URL, status) and how many reviews
were left to fetch, prints the summary, and exits with code **3**. The next run
resumes naturally because the rerun rules skip what's already saved.

### Rerun rules

A review is fetched when any of these holds (the first that applies is its
**reason**, logged and counted in the summary):

| Reason | Rule |
|--------|------|
| `new` | slug not in the manifest |
| `failed` | the last attempt failed temporarily (`status = failed`) |
| `unknown_time` | the manifest has a row but no `fetched_at` (never successfully fetched) |
| `changed` | sitemap lastmod is later than `fetched_at`, or, for a `gone` review, later than `last_attempt_at` |
| `lookback` | lastmod is within the 24 hours before `fetched_at`, the lookback refetch hasn't been done yet, and `now` is at least 24 hours after `fetched_at` |

`--full` fetches every review in the sitemap with reason `full`. A successful
fetch for any reason other than `lookback` resets `lookback_done` to false; a
successful `lookback` fetch sets it to true.

*My call:* a missing sitemap lastmod does **not** trigger a refetch when we
already have a good copy. Otherwise such a review would be fetched on every
run, and a rerun would never be a no-op. Every entry in the sitemap checked on
2026-11-21 had a lastmod, so this is rare. Missing lastmods are logged at DEBUG.

*My call:* the `now ≥ fetched_at + 24h` condition. Refetching an hour later
could hit the same stale cache; waiting a day is what makes the lookback useful.

### Temporary failures and gone pages

Decided with Tyler: failures come in two kinds.

- **Temporary**: network errors, 5xx (a plain 503 included), retries
  exhausted. `status = failed`; retried on every run; counts toward the
  5-in-a-row stop.
- **Gone**: 404 or 410, a redirect away from `/review/`, or a redirect to a
  different review slug. `status = gone`; **not** retried until the review's
  sitemap lastmod is later than `last_attempt_at`; never counts toward the
  stop; listed by slug in the summary so Tyler can look.

Without this split, a handful of dead links in the sitemap would stop every
run before it finished, and look like pushback when it isn't.

### Delisted reviews are kept

*Delisted* means no longer listed in the sitemap. We can't tell whether the
site deleted the review or moved its URL, and we never delete anything
ourselves, so the word describes only what we observe. (Renamed from
"removed", which read like deletion from our own store.)

After **all** review sitemaps downloaded and parsed successfully, any manifest
slug that wasn't in them and has no `delisted_at` gets `delisted_at = now`. A slug
that reappears gets `delisted_at` cleared. Saved HTML is never deleted. If any
sitemap fails, the run stops before fetching pages and marks nothing delisted.

*My call:* a sanity guard. If a run would mark more than 25 reviews **and**
more than 5% of the manifest delisted at once (about 470 on the full site), it
marks nothing, logs the count, and stops with exit 1. A sitemap that parses but
is suspiciously small (a Yoast change, a mostly empty 200) is far more likely
than hundreds of real deletions. The 25-review floor was added after the final
review: with only the 40 rows of a first `--limit` run, 5% alone would have
blocked every run over 3 real delistings. When a large delisting is known to
be real, `--allow-delisting` lets the run mark them anyway, with a warning.

### Storage: gzipped files plus a SQLite manifest

Chosen over a JSON manifest (rewritten per change; fragile or loses progress)
and HTML-in-SQLite (pages hard to inspect by eye during M2). SQLite over DuckDB
for the manifest because the workload is single-row upserts during a long run,
and SQLite in WAL mode allows reading progress from another process while the
scraper writes; DuckDB is kept for analysis and can read the SQLite file
directly. Recorded as ADR 0006.

## Design

### The storage boundary

Decided with Tyler: the scraper knows nothing about how raw data is stored.
Only `storage/` knows formats, paths and databases, so the storage can be
swapped in one place.

- `storage/raw_store.py` defines a `RawStore` Protocol whose methods speak only
  in project terms (slugs, models, datetimes, HTML strings):
  - `load_manifest() -> dict[str, ManifestRecord]`
  - `save_page(entry: SitemapEntry, html: str, fetched_at: datetime, *, lookback: bool) -> None`
  - `record_failure(entry: SitemapEntry, error: str, attempted_at: datetime) -> None`
  - `record_gone(entry: SitemapEntry, reason: str, attempted_at: datetime) -> None`
  - `mark_delisted(slugs: Collection[str], at: datetime) -> None`
  - `mark_relisted(slugs: Collection[str]) -> None`
  - `read_page(slug: str) -> str`
- `GzipSqliteRawStore` implements it. It is the only code that knows about
  `.html.gz` files, SQLite, file paths, WAL mode or `data_dir`.
- `open_raw_store(settings) -> RawStore` in `storage/` is the one place that
  chooses the implementation. `pipeline/scrape.py` calls it and only ever
  holds a `RawStore`. Swapping formats = a new class + one line in the factory.
- `tests/fakes.py` has `InMemoryRawStore`, implementing the same Protocol. The
  pipeline tests run against it, so anything storage-specific leaking into the
  pipeline breaks them. `GzipSqliteRawStore` has its own tests in `tmp_path`.
- The same Protocol is what M2's parser will read pages through.
- `docs/conventions.md` gains the rule: *only `storage/` knows data formats and
  paths; everything else goes through a storage Protocol.*

### Modules

Following the dependency rule (`cli → pipeline → {clients, storage, parsers} → {models, config}`):

| Module | Job | I/O? |
|--------|-----|------|
| `models.py` | `SitemapEntry`, `ManifestRecord`, `FetchReason`, `ScrapeSummary` | no |
| `parsers/sitemap.py` | XML text → review sitemap URLs / `SitemapEntry` list | no |
| `pipeline/fetch_rules.py` | `select_reviews_to_fetch(...)`: the rerun rules | no |
| `clients/coffee_site.py` | fetch sitemap XML and review pages; classify responses | HTTP |
| `storage/raw_store.py` | `RawStore` Protocol, `GzipSqliteRawStore`, `open_raw_store` | files |
| `pipeline/scrape.py` | run a scrape end to end; enforce pushback; build the summary | wires the above |
| `logging_setup.py` | configure terminal + file logging | files |
| `cli.py` | `scrape` command: options, exit codes | no logic |

`pipeline/fetch_rules.py` is in `pipeline/` because it is stage logic, but it
must stay pure (no imports from `clients` or `storage`) so it can be tested with
plain values. *My call.*

### Models

- `SitemapEntry(slug: str, url: str, lastmod: datetime | None)`, frozen.
- `ManifestRecord(slug, url, lastmod, fetched_at, last_attempt_at, status, last_error, lookback_done, first_seen_at, delisted_at)`,
  frozen. `status` is `"ok"`, `"failed"` or `"gone"`. `fetched_at` is the last
  *successful* fetch; `last_attempt_at` is the last attempt of any outcome. All
  datetimes timezone-aware UTC.
- `FetchReason`: `StrEnum` with the six reasons above.
- `ScrapeSummary`: counts of fetched-by-reason, unchanged, failed, gone,
  delisted, relisted, remaining; the slugs that went `gone` this run;
  `stopped_reason: str | None`.

pydantic models, per the roadmap's "typed models at every handoff".

### Sitemap parsing

- Index: keep child sitemaps whose URL path matches `^/review-sitemap\d*\.xml$`.
- Child: keep `<url>` entries whose path matches `^/review/<slug>/$`; slug is
  that segment. Anything else is skipped and counted in a debug log.
- `lastmod` parsed with `datetime.fromisoformat`, converted to UTC; missing or
  unparseable → `None` (which triggers `unknown_time`).
- Standard-library `xml.etree.ElementTree` with the sitemap namespace.

### Client behaviour

`CoffeeScraper` gets per-client retry rules: it retries 408, 500, 502, 504 and
network errors as today, but **does not retry 429 or 503**. Those come back to
the pipeline, which decides whether they are pushback. This needs
`BaseHttpClient` to take its retryable status set from the subclass (a class
attribute), keeping today's set as the default for other clients.

Each page fetch returns one of four results:

- `Fetched(html)`: 200, final URL still under `/review/`, not a challenge page.
- `Failed(error)`: a temporary failure (network error, 5xx, retries
  exhausted).
- `Gone(reason)`: 404, 410, a redirect away from `/review/`, or a redirect to a
  different review slug (*my call:* saving it under the old slug would
  duplicate another review in M2).
- `Pushback(reason)`: 429, 403, or a challenge page (detected from the
  `cf-mitigated: challenge` header or Cloudflare challenge markers in the body;
  exact markers left to the builder, backed by a saved synthetic fixture).

`fetch_review_pages` (the `gather` with the TODO) is replaced: the pipeline
drives fetching so it can stop on pushback.

### Raw store (inside `GzipSqliteRawStore` only)

- Pages: `data/raw/reviews/<slug>.html.gz`, written to a temp file in the same
  directory and moved into place with `os.replace` (atomic).
- Manifest: `data/raw/manifest.sqlite`, WAL mode, table `reviews` (one row per
  slug, columns as `ManifestRecord`, datetimes as ISO 8601 UTC text), schema
  version in `PRAGMA user_version`.
- Per page: write the file, then upsert the row and commit. A crash between the
  two leaves a file newer than its row, so the next run refetches it. That's
  harmless.
- Failures and gone pages update `status`, `last_error`, `last_attempt_at`;
  they keep the previous `fetched_at` and file if there was one.
- The data directory comes from a new setting, `Settings.data_dir` (default
  `data`), so tests point it at `tmp_path`.

### Pipeline

`run_scrape(settings, *, full: bool, limit: int | None, now: datetime) -> ScrapeSummary`:

1. Fetch and parse the index and every review sitemap. Any failure → stop
   (exit 3 if pushback, else exit 1); nothing else happens.
2. Load the manifest; apply delisting marks.
3. `select_reviews_to_fetch(...)`, ordered newest lastmod first *(my call: new
   reviews land first if a run stops early)*; apply `--limit`.
4. Fetch with the client's concurrency; save each result as it arrives; stop on
   pushback.
5. Log and return the summary.

`now` is passed in (the CLI uses the current UTC time), so tests control time
without a time-mocking library.

### Speed controls

Decided with Tyler: one simple setting by default, both settings for
experiments, and every run reports its real load.

- `--rate R` (requests per second). Default 5, must be > 0 and ≤ 10.
- Concurrency is worked out from the rate unless given:
  `concurrency_for_rate(rate) = max(1, ceil(rate))`, one second's worth of
  requests in flight.
- `--concurrency C` may be given with `--rate`; it must be between
  `ceil(rate)` (below that, the rate can't be reached) and `2 × ceil(rate)`
  (above that, extra requests only wait at the rate limiter while holding
  connections). `--concurrency` without `--rate` is rejected.
- The rules live in `CoffeeSiteSettings`, so env vars
  (`COFFEE_REVIEW_COFFEE_SITE__RATE_PER_SEC`, `..._MAX_CONCURRENCY`) follow
  them too. Each rejection says what to do instead.

### Run measurement

Every summary reports what the run actually did to the site:

- **requests**: every HTTP attempt, sitemaps and retries included, counted by
  the client as it sends.
- **elapsed** and **average achieved rate** (requests ÷ elapsed), from a
  monotonic clock passed into `run_scrape`, so tests control it.
- **average response time**: how long the site took to answer, measured
  around the request itself (not the rate-limiter wait). A low achieved rate
  with slow responses means the site is struggling.
- **the rate and concurrency used**, so runs can be compared from their logs.

### CLI and logging

- `coffee-review scrape [--full] [--limit N] [--rate R] [--concurrency C]`.
  `--limit` exists so a first real run can be a handful of pages.
- Exit codes: 0 finished, 1 error, 2 bad options (Click's usage-error code),
  3 stopped by pushback. *My call:* stopped was first planned as 2, which
  collides with Click's usage errors; 3 lets a script tell them apart. A run that
  finishes with some failed pages still exits 0; the failures are in the
  summary and retried next run.
- Logs go to the terminal (INFO) and to `data/logs/scrape-<UTC timestamp>.log`
  (DEBUG). The `httpx` logger is set to WARNING so routine request lines don't
  flood the output.
- Final lines, e.g.
  ```
  scrape finished: 12 fetched (3 new, 9 changed), 9,388 unchanged, 0 failed, 0 gone, 1 delisted
    in 14s · 24 requests · avg 1.7/s · avg response 0.48s · rate 5.0/s, concurrency 5
  ```

## Testing

pytest skills introduced in M1 (from the roadmap): fixtures and fixture scope,
`tmp_path`, async tests, `httpx.MockTransport`, `monkeypatch`, plus
`parametrize` with readable `ids`, and **fakes vs mocks**: a working
in-memory implementation of a Protocol instead of mocked calls.

- **`fetch_rules`**: a parametrized table covering every reason, every
  boundary (exactly 24 h, lastmod equal to `fetched_at`, missing times) and
  `--full`. This is the most important test file in M1.
- **`sitemap` parser**: small saved fixtures in `tests/fixtures/sitemaps/`
  (an index and a child sitemap trimmed to a few entries), plus inline edge
  cases: missing lastmod, non-review URLs, bad dates.
- **`GzipSqliteRawStore`**: round trips in `tmp_path`; upsert; failures keep the old file;
  delisting and relisting; atomic write leaves no temp files.
- **client**: MockTransport for each `Fetched`/`Failed`/`Gone`/`Pushback` case,
  including a challenge page served with 200 and a redirect to the homepage.
- **pipeline**: end-to-end with a fake site (MockTransport) and `InMemoryRawStore`:
  first run, no-op rerun (zero page requests), changed review, pushback stops
  and the next run resumes, sitemap failure marks nothing delisted, six 404s
  in a row don't stop the run and aren't refetched next time.
- **one `network` test**: fetch the real sitemap index and check it lists
  review sitemaps. Skipped by default.

## Done when

From the roadmap, made concrete:

- Against the fake site in tests: a full scrape, then a rerun that makes zero
  page requests; failures retried on the next run; incremental rules covered.
- Against the real site: `coffee-review scrape --limit 20` works and a rerun
  fetches nothing. Tyler runs the first full scrape when ready.
- ADRs 0005 (speed) and 0006 (SQLite manifest, and the storage boundary); roadmap and
  conventions updated.

## Left to the builder

- Exact Cloudflare challenge markers.
- The concurrency mechanism (task group, queue, or similar) as long as pushback
  stops new requests promptly.
- Log message wording beyond the summary line.
- Internal helper names.
