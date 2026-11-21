# Coffee Review Pipeline: Roadmap Design

Date: 2026-11-20
Status: draft, awaiting Tyler's approval

## Intent

This is a self-directed learning project and a portfolio piece. The thing on
display is the **pipeline itself**: how a scraper and data pipeline should be
structured, tested, and documented. It scrapes every review on
coffeereview.com, parses it, cleans it, and enriches the prices (USD conversion
via OpenExchangeRates, inflation adjustment via BLS CPI-U). The final dataset
will later feed a dashboard and then machine-learning work.

Above all, Tyler wants to come out of this knowing how to:

- structure a project like this so it stays easy to follow
- organize code into modules, classes, and functions, and name them well
- apply best practices for web scrapers and data pipelines
- use modern, best-in-class open-source tools
- use **pytest** properly

### Why v2 exists

v1 sprawled. The causes Tyler named, which this design is built to prevent:

1. No good structure from the start (no `src/` layout).
2. Too many issues handled at once.
3. AI-assisted work done without going step by step, so it was hard to keep
   track of what had been done.
4. The codebase grew big and sprawling.

## Goals

- A working pipeline: scrape → parse → clean → enrich, runnable in full or one
  stage at a time, as an incremental update or a full reload, from a CLI.
- Code a reviewer can read in a sitting: clear package boundaries, one job per
  module, consistent naming.
- A real test suite that demonstrates pytest practice, not a token one.
- A visible written trail: roadmap with progress, and a log of decisions and
  why they were made.

## Non-goals (for now)

- Designing the dashboard or the ML work. They are later milestones and read
  only from the final table. The only commitment now is that the final table
  has a documented, stable schema.
- Scheduling, cloud deployment, or distributed execution.
- Real-time or streaming updates.

## Anti-goals (failure even if it "works")

- A step that touches more than one concern, or a commit Tyler hasn't reviewed
  and understood.
- Stages reaching into each other's internals (e.g. the parser making HTTP
  calls, the cleaner reading raw HTML).
- Starting a stage before the previous one meets its definition of done.
- Hammering coffeereview.com: the scraper must be polite.

## Constraints

- Python 3.14, uv, `src/` layout (all already in place).
- Keep and build on the existing `BaseHttpClient` in `clients/http.py`
  (concurrency cap, rate limit, retries with jittered backoff, Retry-After).
- coffeereview.com: ~9,300 review pages, discovered through
  `sitemap_index.xml` (~17 requests). Respect `robots.txt` and keep the request
  rate low. Review page HTML varies across the site's history.
  Scraper speed defaults to 5 requests/second (about 31 minutes for a full
  pass), adjustable from the CLI; see the M1 design and ADR 0005. (An earlier
  draft said 1/second and 2.5 hours; Tyler's experience from v1 showed that
  was more cautious than needed.) Later incremental runs are seconds. Because the raw store is written per
  page and the incremental logic skips what's already saved, an interrupted
  full scrape resumes simply by running again.
- OpenExchangeRates free tier: 1,000 requests/month, USD base only. One
  historical request per review month since 1999 (~330).
- BLS public API without a key: 25 requests/day, limited years per request.
  Series CUUR0000SA0; period M13 (annual average) is discarded.
- No secrets in git; `.env` stays ignored.
- The repo will become a **new public GitHub repo**, which Tyler creates when
  ready. Until then, work is local only. Because it will be public:
  - No scraped data in git: `data/` stays ignored (already in `.gitignore`).
  - Test fixtures under `tests/fixtures/` are kept few and trimmed to the
    markup the parser needs. *My call:* this avoids republishing the
    site's review content in a public repo.
  - The scraper's User-Agent points at the new repo's URL once it exists.
    Until then it uses a placeholder.

## Approaches considered

**A. Thin end-to-end slice first.** Push one review through every stage, then
deepen each stage. Proves the structure early, but forces a shallow first pass
over stages that Tyler knows have many considerations (e.g. HTML variants).

**B. Stage by stage, each finished before the next. (Chosen.)** Build the
scraper until it does its job well, then the parser, and so on. Tyler chose
this because each stage has real depth worth working through properly. The
risk is that a stage grows features before the next one needs them. That risk
is managed by two rules:

- Each stage has a written **definition of done** (below), and work stops when
  it's met. Extra ideas go into the roadmap's backlog, not into the code.
- Each stage's **output contract** (the pydantic model or table schema it
  produces) is written down before the stage is built, so the next stage has a
  fixed thing to build against.

**C. Framework first (Dagster from day one).** Rejected for now: the
framework's conventions would make structural decisions for Tyler and hide the
lessons Tyler wants. It is planned as a later milestone, once there's a plain
pipeline whose pieces it can replace.

## How we work

These are part of the design, not just process, because process is what broke
in v1.

- **Who writes what.** Claude writes the code for each step. Tyler reviews it,
  makes sure they understand it, and asks questions before it's committed.
  Each step's write-up says what changed, why it's structured that way, and
  what to look at closely, so review time goes into learning.
- **One concern per step.** Each step is small, ends in a commit Tyler reviews,
  and doesn't mix concerns (no "parser plus a config fix").
- **Tests alongside code.** A step isn't done until it has tests. For parsers
  and transforms, write the test first.
- **`docs/roadmap.md`** lists milestones and steps as checkboxes, plus a
  backlog. It's updated in the same commit as the work it tracks, so anyone
  (Tyler or an AI session) can see exactly where things stand.
- **`docs/decisions/`** holds short decision records (one file each: context,
  decision, why, alternatives). Written when a choice is made that a future
  reader would wonder about: a tool, a layout, a data format.
- **`docs/conventions.md`** holds the project's structure and naming rules
  (below), so they're decided once and not re-litigated.
- When a choice is made, the reasoning is explained, so Tyler learns how to
  make the decision rather than copying a result.

## Structure

### Packages and the dependency rule

```
src/coffee_review/
  cli.py            # Typer app: parses arguments, calls pipeline functions. No logic.
  config.py         # Settings only.
  models.py         # (or models/ package) pydantic models: the contracts between stages
  clients/          # Talk to the outside world over HTTP. Return raw bytes/JSON.
  storage/          # Read/write each data layer on disk. Know paths and formats.
  parsers/          # Pure functions: raw HTML/XML in, models out. No I/O.
  transforms/       # Pure functions on tables: clean, convert, adjust.
  pipeline/         # One module per stage: wires clients + storage + parsers/transforms.
```

Dependency direction: `cli → pipeline → {clients, storage, parsers, transforms} → models/config`.
Nothing depends on `cli` or `pipeline`. `parsers` and `transforms` import no
I/O at all, which is what makes them easy to test with plain fixtures.

### Data layers

Each stage reads one layer and writes the next. Every layer can be
inspected and regenerated on its own.

| Layer | Written by | Contents |
|-------|-----------|----------|
| `data/raw/` | scrape | gzipped HTML per review + a manifest (URL, slug, sitemap lastmod, fetched-at, status). *My call:* a re-fetch overwrites the saved page; only the latest version is kept |
| `data/parsed/` | parse | one record per review, as fields appear on the page, plus parse errors recorded rather than raised |
| `data/clean/` | clean | typed, normalized table (dates, numbers, price + currency + quantity split out) |
| `data/reference/` | fx, cpi | monthly FX rates and CPI values |
| `data/final/` | enrich | clean table + USD price + inflation-adjusted price; the documented schema dashboard/ML use |

### Naming conventions (starting set, to grow in `docs/conventions.md`)

- Modules are nouns for what they hold (`review_store.py`); functions are verbs
  for what they do (`fetch_sitemap`, `parse_review`, `normalize_price`).
- One name per concept across the codebase (pick "review", "slug", "lastmod"
  and never alias them).
- Classes only where there's state to hold (HTTP clients, stores). Parsing and
  transforming are plain functions.
- Data layer names (`raw`, `parsed`, `clean`, `reference`, `final`) are used
  identically in paths, module names, and CLI commands.

## Tooling (my calls, each to get a decision record)

| Job | Tool | Why |
|-----|------|-----|
| Packaging, env | uv | already in use; current standard |
| Lint/format | ruff | already in use |
| Type checking | mypy (strict, growing) | already in use |
| Tests | pytest, pytest-asyncio, pytest-cov | the learning goal; async clients need async tests |
| HTTP mocking | `httpx.MockTransport` | already supported by the clients; no extra library |
| Snapshot tests | inline-snapshot | lets parser tests pin expected output readably |
| Property tests | hypothesis | for transforms (price/unit parsing) where edge cases matter |
| HTML parsing | selectolax | fast, modern, CSS selectors; fallback selectors for page variants |
| Validation | pydantic (records), pandera (tables) | contracts at each stage boundary |
| Tables | Polars | modern DataFrame library |
| Storage format | Parquet for parsed/clean/reference/final | typed, columnar, readable by DuckDB/Polars |
| Querying | DuckDB | ad-hoc inspection now; dashboard backend later |
| CLI | Typer | already planned |
| Hooks/CI | pre-commit + GitHub Actions | runs ruff, mypy, pytest on every push |
| Later: orchestration | Dagster | milestone after the plain pipeline works |

## pytest as a through-line

Each milestone introduces specific pytest skills, so testing is learned in
context rather than all at once:

- **M0:** `tests/` layout mirroring `src/`, `conftest.py`, running and
  selecting tests, coverage, markers (`slow`, `network`, which never run in CI).
- **M1 (scraper):** fixtures and fixture scope, `tmp_path`, async tests,
  `httpx.MockTransport` for retry/backoff/Retry-After behavior, `monkeypatch`.
- **M2 (parser):** a corpus of saved HTML fixtures, `@pytest.mark.parametrize`
  over them, snapshot tests, `pytest.raises` and testing recorded errors.
- **M3 (clean):** hypothesis property tests, table-level assertions.
- **M4–M5 (reference data, enrich):** testing joins and edge cases (missing
  months, unknown currency), a small end-to-end integration test.

## Milestones

Each milestone ends when its definition of done is met. Steps inside each are
planned when the milestone starts.

### M0. Foundations

Make the skeleton trustworthy before adding to it.

- Fix `config.py`: the invalid `HttpUrl("www.base")`/`HttpUrl("www.sitemap")`
  defaults that crash on import; real base URLs; BLS needs no key.
- Rename `MYPROJECT_` env prefix and rewrite `.env.example` for this project.
- Fix `ForexApiClient` auth param (OpenExchangeRates uses `app_id`).
- Add Typer and wire `[project.scripts]` to the CLI.
- pytest setup, tests for the existing `http.py`.
- pre-commit and GitHub Actions CI.
- `docs/roadmap.md`, `docs/conventions.md`, `docs/decisions/`, a real README.

**Done when:** `uv run coffee-review --help` works, pre-commit and the full
check suite (ruff, mypy, pytest) pass locally, `http.py` is tested, the CI
workflow file is written, and the docs exist. CI going green on GitHub is
checked off once Tyler creates the remote. It doesn't block M1.

### M1. Scraper

- Fetch and parse the sitemap index and child sitemaps into (URL, lastmod).
- Fetch review pages politely; failures returned per page, never ending the run.
- Raw store: gzipped HTML + manifest.
- Incremental logic from NOTES.md (new, unknown times, changed since download,
  24-hour lookback).
- `coffee-review scrape [--full]`.

**Done when:** a full scrape and a no-op incremental rerun both work, failures
are logged and retried on the next run, and the incremental rules are covered
by tests.

### M2. Parser

- Survey HTML variants across the site's history; save a representative
  fixture page for each.
- Write the parsed-record contract.
- Pure parse functions with fallbacks for variants; missing fields are `None`,
  never guessed; failures recorded with the slug.
- `coffee-review parse [--full]`.

**Done when:** every fixture variant parses correctly, a full parse over the
raw layer completes, and the parse-failure rate is reported and understood.

### M3. Clean

- Write the clean-table contract.
- Normalize dates, scores, origins, roast levels; split price into amount,
  currency, quantity, unit.
- `coffee-review clean`.

**Done when:** the clean table validates against its schema and the
transforms have property tests.

### M4. Reference data

- FX client and store: one request per review month, never refetching a past
  month.
- BLS client and store: CPI-U monthly, M13 dropped. *My call:* backfill to
  1999 by requesting explicit year ranges in several keyless requests, well
  inside 25/day. Confirm the per-request year limit against the BLS API docs
  at the start of M4; if keyless can't do it, register for the free key.
- `coffee-review fx`, `coffee-review cpi`.

**Done when:** both are up to date and a rerun costs zero requests.

### M5. Enrich and final table

- Join clean reviews to FX and CPI; USD price and inflation-adjusted USD
  price, with the base period documented.
- Write and publish the final-table schema.
- `coffee-review run [--full]` runs everything in order.

**Done when:** a full run from nothing produces the final table, an
incremental run updates it, and a small end-to-end test passes.

### Later (designed when reached)

- **M6.** Orchestration with Dagster, replacing the hand-wired `pipeline/`.
- **M7.** Dashboard over the final table (DuckDB-backed).
- **M8.** Machine learning on the final table.

## Left to the builder

- The exact fields in each contract, decided during each milestone from what
  the pages actually contain.
- Step-by-step plans inside each milestone.
- Whether `models.py` becomes a `models/` package (when it gets crowded).
- Exact CI matrix and pre-commit hook list.
