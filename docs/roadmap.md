# Roadmap

Built one stage at a time. A stage is finished when its **Done when** line is
true, and only then does the next one start. Ideas that come up along the way go
in the [Backlog](#backlog), not into the current stage.

Full design: [specs/2026-11-20-pipeline-roadmap-design.md](superpowers/specs/2026-11-20-pipeline-roadmap-design.md).
Why things are the way they are: [decisions/](decisions/).

## M0. Foundations

- [x] Settings load with an empty environment; real base URLs; `COFFEE_REVIEW_` prefix
- [x] FX client sends its key in the `Authorization` header, never in the URL
- [x] Typer CLI entry point (`coffee-review --help`, `--version`)
- [x] pytest harness; tests for the HTTP client
- [x] Strict mypy, pre-commit hooks, GitHub Actions workflow
- [x] Roadmap, conventions, decision records, README
- [ ] CI green on GitHub (after the remote is created)

**Done when:** `uv run coffee-review --help` works, pre-commit and the full
check suite pass locally, `http.py` is tested, the CI workflow is written, and
the docs exist.

## M1. Scraper

- [x] Fetch and parse the sitemap index and child sitemaps into (URL, lastmod)
- [x] Fetch review pages politely; failures returned per page, never ending the run
- [x] Raw store: gzipped HTML + SQLite manifest, behind the `RawStore` Protocol
- [x] Incremental logic (new, failed, never fetched, changed, 24-hour lookback; gone pages skipped)
- [x] Stop on pushback (429, 403, challenge page, 5 failures in a row)
- [x] Delisted reviews kept and marked; guard against a broken sitemap (over 25 and 5%; `--allow-delisting` to override)
- [x] `coffee-review scrape [--full] [--limit N] [--rate R] [--concurrency C]`
- [x] Run measurement: requests, elapsed, achieved rate, mean response time
- [x] First real run with `--limit 20` (2026-11-21: 2 runs, 40 pages, 0 failures, avg response ~0.5s)
- [x] First full scrape (2026-11-22: 9,440 reviews, 261 MB; 4 encoded-slug pages fixed and refetched)

**Done when:** a full scrape and a no-op incremental rerun both work, failures
are logged and retried on the next run, and the incremental rules are covered
by tests.

## M2. Parser

- [x] Survey HTML variants across the site's history; 10 trimmed fixtures (prose replaced with placeholders)
- [x] Parsed-record contract: `RoastedReview` and `GreenReview`, one Parquet dataset each
- [x] One alias table for every name a field has had; unknown labels kept in `extra`
- [x] Pure page parser; missing fields are `None`; problems recorded as issues
- [x] Parse report: coverage, unknowns, issues, value shapes
- [x] `coffee-review parse` (always a full re-parse; about 6 seconds)
- [x] First real parse (2026-11-22): 9,440 pages → 9,330 roasted (42 sold green), 110 green; 0 unknown labels; 1 issue, explained below

Explained, not bugs (from the first report):

- `peaberry-green` has no rating: the site's score element is empty.
- `aged-sumatra-2` (no blind assessment or notes) and `kenya-karagoto` (no notes): the site has empty paragraphs under those headings.
- `chocolate-espresso-blend` and `golden-geisha-blend` have no Roaster Location row.

**Done when:** every fixture variant parses correctly, a full parse over the raw
layer completes, and the parse-failure rate is reported and understood.

## M3. Clean

Starting input: the value shapes in `data/parsed/report.md`. For example, every
review date is `a 9999` ("Month Year"); prices come in `$…/12 ounces`,
`NT $…/200 grams`, `NA` and more; Agtron uses `0/0`, `/` and `NA/NA` for
unknown, and some second values exceed 100; 1997–1999 scores use decimals.

- [x] Clean-table contract: pandera models for roasted, green and the cleaning log
- [x] Dates (`review_month`), rating and scores, roast level, Agtron (2002–03 swap, typo patterns), text sections
- [x] Places: origin country and region, roaster city, region and country; complete ISO country list, close spellings flagged
- [x] Price: amount, currency (ISO codes; `$` and `¥` read from the roaster's country), quantity, unit, grams; green lot sizes in kg
- [x] Cleaning log, report with a *To review* list marked new since the last run, and a warning when anything is new
- [x] `coffee-review clean` (always a full clean; under a second)
- [x] First real clean (2026-11-22): 9,440 reviews; contract passes; 0 values lost without a rule; 0 of 8,239 disclosed origins unplaced; 1 of 7,315 real prices unparsed; Agtron 71 swapped, 6 typos corrected

Left as they are, listed in the report (guessing would be wrong):

- 30 multipack prices (`six 8-ounce cans`, `12 9.5-ounce cans`) keep amount and currency, no quantity. A follow-up could multiply them out.
- `.8 ounces`, `.12 ounces`, `$70/00/4 ounces`, `$19..75/12 ounces`: typos with more than one reading.
- `#23.00` (probably `$`) and one price with no symbol: currency unknown.
- 7 roaster locations that aren't places, or are a city alone (`Swiss Water Process`, `Grower`, `$12.75/12 ounces`, `Los Angeles`).
- Lot sizes in boxes or bags (`6 boxes of 76 lbs each`).

**Done when:** the clean table validates against its schema and the transforms
have property tests.

## M4. Reference data

- [ ] FX client and store: one request per review month, never refetching a past month
- [ ] BLS client and store: CPI-U monthly, M13 dropped; confirm keyless year-range limit first
- [ ] `coffee-review fx`, `coffee-review cpi`

**Done when:** both are up to date and a rerun costs zero requests.

## M5. Enrich and final table

Starting input: `data/clean/`, with `price_currency` as ISO codes and
`review_month` on the 1st (the FX and CPI join keys). dbt-duckdb is revisited
here ([ADR 0008](decisions/0008-clean-in-polars.md)).

- [ ] Join clean reviews to FX and CPI; USD price and inflation-adjusted USD price, base period documented
- [ ] Write and publish the final-table schema
- [ ] `coffee-review run [--full]` runs everything in order

**Done when:** a full run from nothing produces the final table, an incremental
run updates it, and a small end-to-end test passes.

## Later

Designed when reached.

- **M6.** Orchestration with Dagster, replacing the hand-wired `pipeline/`
- **M7.** Dashboard over the final table (DuckDB-backed)
- **M8.** Machine learning on the final table

## Backlog

- ~~M1: is capping a 429 `Retry-After` at `backoff_max_seconds` polite enough?~~ Resolved: the scraper never retries a 429; it stops the run.
- Measure the right `--rate` from real runs and record it (ADR 0005 allows up to 10/s).
