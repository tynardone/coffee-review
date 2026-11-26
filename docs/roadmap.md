# Roadmap

Built one stage at a time. A stage is finished when its **Done when** line is
true, and only then does the next one start. Ideas that come up along the way go
in the [Backlog](#backlog), not into the current stage.

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

## M4. Economic data

Why: [ADR 0009](decisions/0009-keep-raw-economic-responses.md).

- [x] Every response saved as received under `data/raw/`; tidy tables rebuilt from them on every run
- [x] FX: the 1st of every month from 2010 (first non-USD price 2010-03), USD base, once each UTC day is over
- [x] CPI-U `CUUR0000SA0` from 1997, keyless BLS v1 (10 years a request), M13 dropped; months the source marks unavailable not asked again
- [x] `coffee-review fx`, `coffee-review cpi` (exit 3 when the source refuses or is down)
- [x] Simplified (2026-11-23): with only two fixed sources, the source registry, Protocols, fakes and gap tables went
- [x] First real runs (2026-11-23): CPI took 3 requests for 357 months, 1997-01 to 2026-10, with October 2025 missing (the BLS collected no data during the 2025 government shutdown); FX took 203 requests for 33,925 rows; both reruns took none

**Done when:** both are up to date and a rerun costs zero requests.

Notes for M6: the one priced review from before the FX table (2009-02) is in
USD, so it needs no rate; reviews dated October 2025 have no CPI month.

## M5. Roaster names

Why: one roaster is often written several ways (`JBC Coffee Roasters` and
`JBC`, `El Gran Cafe` and `El Gran Café`), which splits it in two for the
dashboard and the models. The first clean has 1,621 roaster spellings across
9,350 roasted reviews. In scope: the same name written differently. Out of
scope: renamed, merged or sold roasters.

Part of the clean stage. How it works: [explainers/roaster-names.md](explainers/roaster-names.md).

- [x] Design written and approved (2026-11-23)
- [x] Spellings that differ only in case, accents, punctuation or words like *Coffee Roasters* become one name, unless their roasters are in different countries (then they're listed for review)
- [x] Close spellings (rapidfuzz) merge at a score of 0.90 or more; from 0.85, or 0.80 in the same city, they're listed for review
- [x] Roasters in different countries are never merged automatically, even through a chain of merges
- [x] A person's decisions are kept in `roasters.csv` (same roaster) and `distinct_roasters.csv` (different roasters), and never asked again
- [x] The most common spelling is kept, the original stays in the cleaning log, and the thresholds are settings
- [x] First real clean (2026-11-25): 1,696 spellings became 1,550 roasters; the 17 pairs listed for review were decided (6 the same roaster, 11 different) and one kept name fixed, leaving 1,544 roasters
- [x] Review sheet (2026-11-25): pairs are decided in `data/clean/roaster_review.csv` and written by `coffee-review roasters apply`

**Done when:** every merge is a shared key, a close spelling above the
threshold with no location conflict, or a row in `roasters.csv`; the review
list is worked through; and the rules have tests.

## Origin growing regions

Why: the region text below the country is the page's own (1,357 different
values), too scattered to compare reviews or use in the models. Part of the
clean stage. How it works: `transforms/growing_regions.py`.

- [x] Design written and approved (2026-11-26)
- [x] `origin_growing_region` in both clean tables: one standard region from
  `origin_regions.csv`, for the 15 countries with 100+ reviews, one level per
  country (Ethiopia's trade regions, Colombia's departments, Brazil's states,
  Guatemala's and Costa Rica's official coffee regions…)
- [x] Two regions in one text are `Multiple`; region text naming nothing
  known is listed in the report to review
- [x] First real clean (2026-11-26): 5,762 of 9,464 reviews got a region (113
  regions, plus 128 `Multiple`); in every covered country 90–100% of the
  reviews with region text. 83 reviews are left to review: farm and village
  names, text that isn't a place, and places whose region isn't certain
  (San Martín Jilotepeque, Kersa, Tobosi)
- [x] A broader name beside a word the table doesn't know (`Djimmah, Oromia`)
  is listed for review, so new spellings aren't hidden

**Done when:** every covered country with 150+ reviews names a region for at
least 85% of its reviews with region text, and the rules have tests.

## M6. Enrich and final table

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

- **M7.** Orchestration with Dagster, replacing the hand-wired `pipeline/`
- **M8.** Dashboard over the final table (DuckDB-backed)
- **M9.** Machine learning on the final table

## Backlog

- Measure the right `--rate` from real runs and record it (ADR 0005 allows up to 10/s).
- Prices in review text: before 2009 the price field is `NA`, but 78 of 1,738 older pages give a dollar price in the review itself (`Twelve-ounce can $3.29, or $4.39 per pound`). Extracting them is parse/clean work, often with two prices per page. CPI already covers 1997 onwards.
