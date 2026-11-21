# Roadmap

Built one stage at a time. A stage is finished when its **Done when** line is
true, and only then does the next one start. Ideas that come up along the way go
in the [Backlog](#backlog), not into the current stage.

Full design: [specs/2026-11-20-pipeline-roadmap-design.md](superpowers/specs/2026-11-20-pipeline-roadmap-design.md).
Why things are the way they are: [decisions/](decisions/).

## M0. Foundations

- [x] Settings load with an empty environment; real base URLs; `COFFEE_REVIEW_` prefix
- [x] FX client sends its key as `app_id`
- [x] Typer CLI entry point (`coffee-review --help`, `--version`)
- [x] pytest harness; tests for the HTTP client
- [x] Strict mypy, pre-commit hooks, GitHub Actions workflow
- [x] Roadmap, conventions, decision records, README
- [ ] CI green on GitHub (after the remote is created)

**Done when:** `uv run coffee-review --help` works, pre-commit and the full
check suite pass locally, `http.py` is tested, the CI workflow is written, and
the docs exist.

## M1. Scraper

- [ ] Fetch and parse the sitemap index and child sitemaps into (URL, lastmod)
- [ ] Fetch review pages politely; failures returned per page, never ending the run
- [ ] Raw store: gzipped HTML + manifest
- [ ] Incremental logic (new, unknown times, changed since download, 24-hour lookback)
- [ ] `coffee-review scrape [--full]`

**Done when:** a full scrape and a no-op incremental rerun both work, failures
are logged and retried on the next run, and the incremental rules are covered
by tests.

## M2. Parser

- [ ] Survey HTML variants across the site's history; save a trimmed fixture per variant
- [ ] Write the parsed-record contract
- [ ] Pure parse functions with fallbacks for variants; missing fields are `None`; failures recorded with the slug
- [ ] `coffee-review parse [--full]`

**Done when:** every fixture variant parses correctly, a full parse over the raw
layer completes, and the parse-failure rate is reported and understood.

## M3. Clean

- [ ] Write the clean-table contract
- [ ] Normalize dates, scores, origins, roast levels; split price into amount, currency, quantity, unit
- [ ] `coffee-review clean`

**Done when:** the clean table validates against its schema and the transforms
have property tests.

## M4. Reference data

- [ ] FX client and store: one request per review month, never refetching a past month
- [ ] BLS client and store: CPI-U monthly, M13 dropped; confirm keyless year-range limit first
- [ ] `coffee-review fx`, `coffee-review cpi`

**Done when:** both are up to date and a rerun costs zero requests.

## M5. Enrich and final table

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

Nothing yet.
