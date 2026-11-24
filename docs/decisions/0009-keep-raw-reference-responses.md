# 0009. Reference data keeps every raw response and rebuilds from it

Date: 2026-11-22 · Status: accepted (simplified 2026-11-23)

## Context

M5 converts every price to US dollars and adjusts it for inflation. The method
is **convert, then adjust**: a price is converted at its review month's
exchange rate, then adjusted with US CPI-U, so the result reads "what that
coffee cost an American buyer, in today's dollars". Adjusting each price with
its own country's CPI first was ruled out: it would need a CPI source for
every currency.

That needs two reference tables: monthly exchange rates (OpenExchangeRates,
free plan: 1,000 requests a month, one day per request) and US CPI-U (the BLS,
keyless: 25 requests a day, 10 years per request). Data fetched once shouldn't
be paid for twice.

## Decision

Each stage, `fx` and `cpi`, works in three steps:

1. Parse every saved response. A saved file that won't parse counts as missing.
2. Fetch only what isn't saved yet, saving each answer exactly as received
   (under `data/raw/fx/` or `data/raw/cpi/`) before using it.
3. Rebuild the tidy table in `data/reference/` in full from the saved files,
   check it against its contract, and write it.

The raw files are the source of truth; the tables are never edited.

The alternatives were a tidy table only, fetched and parsed in one step (less
code, but a parser bug means fetching the history again and spending quota),
and DuckDB or SQLite tables (ADR 0006 already chose Parquet after the raw
layer).

Details that follow from it:

- **Every date is UTC**, as OpenExchangeRates' are. A day's rate is fetched
  only once that UTC day is over.
- **Gaps are recorded, not retried.** OpenExchangeRates' `not_available` is
  saved like any answer; the BLS marks October 2025 (no data collected during
  the 2025 lapse in appropriations) as `-` with footnote X. Neither is asked
  for again; the period is simply absent from the table, and the run's summary
  line names it.
- **A month not out yet is tried at most once a day.** A year a response
  saved today already covered isn't asked for again until tomorrow.
- **The source stops the run, not the code.** A refusal, a bad key or a
  source that's down stops it cleanly (exit 3) with everything saved kept; so
  do three answers in a row that won't parse, rather than spend the month's
  quota on answers that are thrown away.

**Not built:** a registry of interchangeable sources. A first version had one
(Protocols, a source per entry, a `source` column); it was removed as more
machinery than two fixed APIs need. Replacing a source means a new client and
parser, and the stage function that calls them.

## Consequences

- A rerun costs zero requests (tested, and confirmed on the first real runs:
  3 BLS requests for 1997–2026, 203 OpenExchangeRates requests for 2010–2026,
  then none).
- A parser fix is applied by rerunning; it reads the saved files. The first
  real CPI run found the BLS `-`/X marker, and the fix was checked by
  rebuilding from the saved responses with no requests.
- Every number in a reference table can be traced to the file it came from.
- The data is never committed (`data/` is gitignored). OpenExchangeRates'
  terms allow use but not resale of its data.
