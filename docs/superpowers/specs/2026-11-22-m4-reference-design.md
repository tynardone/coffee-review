# M4 Reference Data: Design

Date: 2026-11-22
Status: approved by Tyler, 2026-11-22 (builder check done; its answers are my calls, listed in the review)
Parent: [pipeline roadmap design](2026-11-20-pipeline-roadmap-design.md), milestone M4.
Used by: M5, which joins clean reviews to these tables on `review_month`.

## Intent

M5 turns every price into **inflation-adjusted US dollars**. M4 fetches the two
reference tables that needs, keeps them up to date cheaply, and never spends
API quota on data it already has:

- **Exchange rates**: how many units of each currency one US dollar bought on
  the 1st of each month.
- **US CPI-U**: the consumer price index for each month.

The method (Tyler, 2026-11-22): **convert, then adjust**. A price is converted
to USD at its review month's rate, then adjusted with US CPI. So M4 needs one
FX table and one US CPI series, not a CPI per country.

This is also a **data-engineering portfolio piece**, and two properties should
be easy to see and defend in review:

1. **Raw first** (Tyler, option B): every API response is kept exactly as
   received, and the tidy tables are always rebuilt from those files. A
   parsing bug is fixed by rerunning, with no requests.
2. **Swappable sources** (Tyler): OpenExchangeRates and the BLS are the first
   adapters, not assumptions. Replacing either is a new client and parser
   behind a Protocol, and a setting.

## What the data needs (checked 2026-11-22)

- Prices in the price field start **2009-02**; the first non-USD price is
  **2010-03**. 192 months have a non-USD price; 19 currencies appear (USD
  5,435 rows, TWD 1,474, CAD 178, HKD 73, CNY 33, …).
- Before 2009 the price field is the literal `NA`, but 78 of 1,738 older pages
  mention a dollar price in the review text. Extracting those is **backlog**,
  not M4; CPI starts in 1997 so they're covered if it ever happens.

## Goals

- `coffee-review fx` and `coffee-review cpi` bring each table up to date.
- **Done when** (roadmap): both are up to date and a rerun costs zero requests.
- Raw responses under `data/raw/`, tidy tables under `data/reference/`, both
  behind store Protocols with in-memory fakes held to the same contract tests.
- A pandera contract for each tidy table; both appear in the generated data
  dictionary.
- Swapping a source touches: one client, one parser, one registry entry, one
  setting. Nothing in storage, the tidy tables, or M5.

## Non-goals

- Converting or adjusting prices, choosing the CPI base period (M5).
- CPI for countries other than the US (ruled out by convert-then-adjust).
- Monthly-average exchange rates (the free plan gives one day per request;
  an average would cost ~30× the requests).
- Prices in review text (backlog).
- A BLS registration key (keyless is enough; adding one later is a settings
  and client change).

## Anti-goals

- Fetching anything already stored. A rerun that sends a request it didn't
  need is a bug.
- A tidy table that disagrees with the raw files, or is edited in place.
- Source-specific names leaking past the adapter (no `oxr_`, `bls_` columns,
  no `app_id` anywhere but the client).
- An API key in a URL, a log line or an error message (already a rule since M0).

## Approaches considered

| | Approach | Trade-off | Verdict |
|---|---|---|---|
| A | Tidy table only; the adapter fetches and parses in one step | Least code; a parser bug means refetching history and spending quota | Not chosen |
| **B** | **Raw responses saved, tidy table rebuilt from them** | More code (a raw store, a parser per source); replayable, traceable, matches scrape → parse | **Chosen by Tyler** |
| C | DuckDB/SQLite tables | ADR 0006 already settled Parquet after the raw layer; the store Protocol keeps it swappable anyway | Dropped |

## The sources (facts checked against their docs, 2026-11-22)

**OpenExchangeRates, free plan**
- `GET historical/YYYY-MM-DD.json`; history back to 1999-01-01.
- 1,000 requests a month, not carried over; base USD only (`base` and
  `symbols` are paid). Each response has every currency (~170) against USD,
  with `USD: 1`.
- Shape: `{disclaimer, license, timestamp, base, rates: {CODE: number}}`.
- Auth: `Authorization: Token <app_id>` (already how `ForexApiClient` works).
- Errors: `{error: true, status, message, description}`. 401
  `missing_app_id`/`invalid_app_id`; 429 `not_allowed`; 403
  `access_restricted` (over-use); 400 `not_available` (bad or unavailable
  date). Running out of monthly quota isn't documented.
- Rates for a day are "the last values published for a given UTC day", and
  asking for today returns the *latest* rates, which still change.
- Terms: use is allowed; reselling the data isn't. The data is never
  committed (it's under `data/`, which is gitignored).

**BLS Public Data API, keyless**
- Keyless means **v1**: `POST https://api.bls.gov/publicAPI/v1/timeseries/data/`.
  The current `BLSApiSettings.base_url` points at v2 and changes to v1.
- 25 queries a day, **10 years per query**, 50 requests per 10 seconds.
- Body: `{"seriesid": ["CUUR0000SA0"], "startyear": "1997", "endyear": "2006"}`.
- Shape: `{status, responseTime, message: [..], Results: {series: [{seriesID,
  data: [{year, period, periodName, value, footnotes: [{code, text}]}]}]}}`.
  `value` is a string. A bad request can still say `REQUEST_SUCCEEDED`, with
  the problem in `message` and an empty `data`.
- Series: `CUUR0000SA0`, CPI-U, all items, US city average, not seasonally
  adjusted, 1982–84 = 100. This series isn't revised after release.
- Periods `M01`–`M12` are months. `M13` (annual average) and anything else
  are dropped by the parser.

## Design

### Shape

The source split follows the dependency rule that already exists:

```
cli  →  pipeline/reference.py  ──→ clients/forex_api.py, clients/bls_api.py   fetch raw text
                               ──→ parsers/fx_openexchangerates.py, parsers/cpi_bls.py   text → models, pure
                               ──→ storage/reference_store.py   raw files + tidy Parquet
                               ──→ transforms/reference.py   pure: what to fetch, merge rows
models/reference.py   FxRate, CpiMonth, the pandera contracts, the Protocols' types
```

- **Client Protocols** (`clients/reference.py`), async like `BaseHttpClient`
  and used as async context managers: `async FxClient.fetch_day(day: date) ->
  str` and `async CpiClient.fetch_years(series: str, start: int, end: int) ->
  str`. They return the response text exactly as received when the source
  *answered* (data, or "no data for that"), and raise a typed error (below)
  when it didn't. Because `BaseHttpClient.request` raises on any 4xx and
  retries 429, each client narrows `retryable_status` to 5xx/timeouts (as the
  scraper does) and catches `httpx.HTTPStatusError` to read the body and pick
  the typed error.
- **Parsers** are plain functions with one signature per kind:
  `parse_fx(text: str, day: date) -> FxParsed` and
  `parse_cpi(text: str, series: str) -> CpiParsed`. Each result holds the good
  rows, the periods the response *covered* (asked for and answered, with or
  without a value), and issues for values it dropped. Pure, tested with
  small hand-written JSON samples (no real responses in the repo).
- **A source** is a registry entry in the pipeline naming its client, the
  settings section the client is built with, and its parser:
  `FX_SOURCES = {"openexchangerates": FxSource(client=ForexApiClient, settings=lambda s: s.forex_api, parse=parse_fx_openexchangerates)}`,
  likewise `CPI_SOURCES = {"bls": ...}`. Settings choose by name:
  `COFFEE_REVIEW_REFERENCE__FX_SOURCE=openexchangerates`,
  `COFFEE_REVIEW_REFERENCE__CPI_SOURCE=bls` (plain strings in `config.py`,
  which can't import `pipeline`). An unknown name fails when the command
  starts, before any request, listing the known ones. A swap is therefore:
  a client (with its settings section), a parser, a registry entry, a setting.
- **The source's name** is the folder its raw files go in and the `source`
  column in the tidy table. It's the only place a source's identity appears.

### Raw files

```
data/raw/fx/<source>/<YYYY-MM-DD>.json                 one per day fetched
data/raw/cpi/<source>/<series>/<fetched-at>_<start>-<end>.json   one per request
```

- Written atomically (`storage/atomic.py`) for **every response in which the
  source answered**, including "no data": an OpenExchangeRates 400
  `not_available`, a BLS `REQUEST_SUCCEEDED` with a month absent. That's what
  the source said, and it's what lets the next run know it already asked.
  Responses where the source didn't answer (auth, refusal, 5xx, network) are
  logged, never saved.
- FX files are keyed by the day they describe: there's exactly one.
- CPI files are keyed by *when* they were fetched and the years asked for,
  because the same month can arrive in several responses (the current year is
  asked for again each month).
- Rebuild merge order: rows from the configured source first, newest file
  first within a source; then other sources fill what's left.
- `docs/conventions.md` changes: `data/raw/` holds scrape's pages *and* the
  reference responses, in separate folders and separate stores.

### Gaps: periods a source will never have

Some periods don't exist at the source. The BLS published no October 2025
CPI (the 2025 government shutdown; confirm when building), and
OpenExchangeRates may answer `not_available` for a day. Without a rule,
these would be asked for on every run, and the zero-request rerun could
never pass.

- **FX:** a day whose saved response is `not_available` is a **gap**: stored,
  never asked again (delete the file to retry it).
- **CPI:** a month is a gap when a saved response covered its year *and*
  contains a later month. So a response for 2025 with September and November
  but no October settles October. A missing month with no later month is
  just "not out yet" (the newest-month rule below).
- Gaps are written to the tidy layer as `fx_gaps.parquet` / `cpi_gaps.parquet`
  (period, source, what the source said) and listed in the summary line, so
  M5 can decide what a review in a gap month gets. Deciding that is M5's job.

### Tidy tables (`data/reference/`)

`fx.parquet`: one row per (day, currency).

| Column | Type | Meaning |
|---|---|---|
| `day` | Date | The day the rate is for (always the 1st, today) |
| `currency` | String | ISO 4217 code |
| `units_per_usd` | Float64 | Units of the currency one USD bought, > 0 |
| `source` | String | Which source it came from |

`cpi.parquet`: one row per (series, month).

| Column | Type | Meaning |
|---|---|---|
| `month` | Date | The 1st of the month |
| `series` | String | The series id (`CUUR0000SA0`) |
| `index_value` | Float64 | The index, > 0 (1982–84 = 100 for this series) |
| `source` | String | Which source it came from |

Contracts in `models/reference.py` (pandera, Polars backend), with unique
keys, positive values, the first-of-month check reused from M3, ISO-shaped
currency codes. The descriptions feed the data dictionary.

Both tables are **rebuilt in full from the raw files on every run** (≈200
small files, well under a second), then validated, then written atomically.

A bad value (a zero or negative rate, a non-numeric index, a malformed
currency code) is **dropped by the parser** and logged as an issue with the
file and value, and counted in the summary line; the rest of that response
is kept. So the contract checks what the parsers kept and should never fail
on real data. If it does, it's a bug: nothing is written and the command
exits 1, as `clean` does.

### What to fetch (pure, in `transforms/reference.py`)

**Every date here is UTC.** OpenExchangeRates dates and timestamps are UTC
with no daylight saving, and it asks clients to convert local time to UTC
before requesting. So "today" is always `datetime.now(UTC).date()`, passed in
by the pipeline (the pure functions take `today` as an argument, so tests
pin it), never the machine's local date. Example: at 8 p.m. in New York on
30 November it's already 1 December in UTC, and 1 December isn't over, so its
rate isn't asked for yet. The same UTC "today" drives the CPI same-day rule
and the CPI raw file names (`<fetched-at>` is a UTC timestamp, like every
other timestamp in the project).

**FX**
- Wanted days: the 1st of every month from `fx_start` (setting, default
  `2010-01-01`) to the current month, **but only days that are already over
  in UTC**. On the 1st itself, today's month waits until tomorrow, because
  "today" still returns changing rates.
- Missing = wanted days with no raw file **for the configured source**
  (a gap file counts as present).
- Fetch missing days in order, oldest first.

**CPI**
- Wanted months: `cpi_start` (default `1997-01`) up to the last month that
  could be published (the month before the current one).
- Missing months are grouped into requests of at most 10 years each (the
  keyless limit, a property of the source, not a constant in the pipeline).
- Missing = wanted months that are neither stored nor gaps.
- **The newest month** is the one judgement call: a month's CPI comes out mid
  next month, so on 2026-11-05 October isn't out yet. Rule: if the only
  missing month is last month and a CPI response covering it was already
  saved today (from the raw file names; an empty answer counts), send
  nothing. So reruns on the same day cost zero, and the next day tries once.
- A row a source marks as not final (BLS footnote code `P`) is stored but
  treated as missing, so it's asked for again. CPI-U normally has none; this
  keeps a future source honest.

### Swapping a source

- Set the new name in settings. Missing days/months are worked out **for that
  source's folder**, so the new source fills in everything it doesn't have.
  Rerun: zero requests once it's complete.
- On rebuild, rows from the configured source win; rows from other sources
  fill days the configured source doesn't have. The `source` column says
  which is which.
- `--full` is not needed for a swap: it's simply "the new source has nothing
  yet". (So no `--full` flag in M4; the raw files are never deleted by code.)

Cost of the swap rule if wrong: the first run after a swap re-requests the
full history from the new source (≈200 FX requests, 3 CPI). That's the honest
cost of switching providers, and it's within the free limits.

### Failures

- Typed errors from the clients, for when the source didn't answer:
  `SourceAuthError` (401: missing or bad key), `SourceRefusedError` (403,
  429: quota, over-use, `not_allowed`), `SourceUnavailableError` (retries
  exhausted on 5xx, timeouts, network: `RetriesExhaustedError` wrapped).
  "No data" answers are not errors; they're saved (see Gaps).
- Any of the three: **stop cleanly** at the first one, keep every file
  already saved, rebuild the tidy table from what exists, exit 3 ("stopped by
  the source"), like scrape's pushback. Quota exhaustion is undocumented, so
  403 and 429 both mean stop, never retry. The next run carries on from where
  this one stopped.
- A response that isn't valid JSON or lacks the expected fields: logged with
  its period, not saved, the run carries on; that period stays missing and is
  tried next run. Counted as failed in the summary line, exit 0.
- `fx` without an API key: the existing `MissingApiKeyError` message, exit 1,
  before any request. `cpi` needs no key.
- Retries, rate limiting and timeouts are the existing `BaseHttpClient`'s.
  BLS keeps its settings (0.5/s, one at a time); OpenExchangeRates gets the
  same polite defaults (my call: no reason to hurry ~200 requests once).

### CLI output

One summary line each, plus the run log under `data/logs/`, like `parse` and
`clean` (my call: no Markdown report; there's nothing for a person to review):

```
fx finished: 3 fetched, 201 already stored, 0 failed · fx.parquet 34,170 rows, 2010-01-01 to 2026-11-01
cpi finished: 1 request, 359 months stored · cpi.parquet 1997-01 to 2026-10
```

A rerun prints `0 fetched` / `0 requests`.

## Tests

- Parsers: hand-written samples for a normal response, `M13` dropped,
  footnote `P`, an empty `data` with a message, a non-numeric value, a
  currency with a zero rate (rejected by the contract, not silently kept).
- What-to-fetch: property tests (hypothesis) over today's date and the set of
  stored days: never asks for a stored day, never for today or the future,
  always for every missing past 1st; CPI chunks never exceed 10 years and
  cover exactly the missing years.
- UTC: with the clock at 2026-11-30 20:00 in New York (2026-12-01 01:00 UTC),
  `fx` doesn't ask for 2026-12-01; at 2026-12-02 00:00 UTC it does.
- **Zero-request rerun:** run `fx` and `cpi` against an `httpx.MockTransport`
  that counts requests; run again; assert the count didn't change. This is the
  roadmap's "done when", as a test.
- Swap: two fake sources; switching fills only what the second lacks;
  rebuild prefers the configured one.
- Failures: 401, 403, 429 and exhausted retries stop with exit 3 and keep
  earlier files; a 429 is never retried; 400 `not_available` is saved as a
  gap and not asked again; the key never appears in log output.
- Gaps: a CPI response missing a month between two present ones settles it,
  and the rerun is still zero requests.
- Stores: contract tests run against the Parquet/file store and an in-memory
  fake, like the other layers.

## Docs

- **ADR 0009**: keep raw API responses and rebuild reference tables from them;
  sources behind Protocols. Records approach A vs B and why.
- Architecture page: the M4 stage diagram and the pipeline-at-a-glance arrows.
- Data dictionary: the tidy tables and gap tables (generated from their
  contracts), plus a short hand-written entry for the raw folders in the
  generator, like its existing `EXTRA_FIELDS`.
- Conventions: new modules in the package list; `data/raw/` now has three
  writers' folders. Roadmap: M4 items reworded to this design (every 1st from
  2010, CPI from 1997; this spec supersedes "one request per review month"
  and "1999"); a backlog line for review-text prices. README: the two
  commands.

## Left to the builder

- Exact model and function names beyond the ones above, and how the registry
  is typed.
- Whether FX and CPI share one store class or have two; one Protocol each
  is the requirement.
- Summary-line wording, within the shape above.
