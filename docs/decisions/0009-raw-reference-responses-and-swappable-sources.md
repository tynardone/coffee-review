# 0009. Reference data keeps every raw response, behind swappable sources

Date: 2026-11-22 · Status: accepted

## Context

M5 converts every price to US dollars and adjusts it for inflation. The method
is **convert, then adjust**: a price is converted at its review month's
exchange rate, then adjusted with US CPI-U, so the result reads "what that
coffee cost an American buyer, in today's dollars". That needs two reference
tables: monthly exchange rates (OpenExchangeRates) and US CPI-U (the BLS).
Adjusting each price with its own country's CPI first was ruled out: it would
need a CPI source for every currency (about 18 agencies).

Both sources are limited: OpenExchangeRates' free plan allows 1,000 requests a
month and one day per request; keyless BLS allows 25 requests a day and 10
years per request. Data fetched once shouldn't be paid for twice.

Tyler also wanted either source to be easy to replace later.

Three ways to store the data were weighed:

| | Approach | Trade-off |
|---|---|---|
| A | Tidy table only: fetch and parse in one step | Least code; a parser bug means fetching the history again and spending quota |
| **B** | **Save every response as received; rebuild the tidy tables from them** | More code (a raw store, a parser per source); replayable and traceable |
| C | DuckDB or SQLite tables | ADR 0006 already chose Parquet after the raw layer, and the store Protocol keeps it swappable anyway |

## Decision

**B.** Each stage, `fx` and `cpi`, works in three steps:

1. Parse every saved response. A saved file that won't parse counts as missing.
2. Fetch only what the configured source doesn't have yet, saving each answer
   (including "no data for that") under `data/raw/fx/` or `data/raw/cpi/`
   before using it.
3. Rebuild the tidy tables in `data/reference/` in full from the saved files,
   check them against their contracts, and write them.

The raw files are the source of truth; the tidy tables are never edited.

**Sources sit behind Protocols.** A source is a registry entry in
`pipeline/reference.py`: a client (`FxClient` or `CpiClient`), the settings it
is built from, and a pure parser returning the shared models. Swapping one is
a new client, a parser, an entry and a setting
(`COFFEE_REVIEW_REFERENCE__FX_SOURCE`). The source's name appears only as its
raw folder and the tables' `source` column.

**A swap doesn't refetch history.** What's missing is worked out per source,
so the new source fills in only what it doesn't have; on rebuild, the
configured source's rows win and other sources fill the rest. The first run
after a swap fetches the whole history from the new source (about 200 FX
requests, 3 CPI), which is the honest cost of switching and within the free
limits. A retired source keeps its registry entry so its saved history stays
readable.

**Gaps are recorded, not retried.** A period the source answers but has no
data for (OpenExchangeRates' `not_available`; BLS's `-` with footnote X for
October 2025, when no data was collected) is saved, written to a gaps table in
the source's own words, and never asked for again. What M5 does with a review
in a gap month is M5's decision.

**Every date is UTC**, as OpenExchangeRates' dates are. "Today" comes from the
UTC clock and is passed into the pure planning functions, so a rate for a day
is asked for only once that UTC day is over.

## Consequences

- A rerun costs zero requests (tested, and confirmed on the first real runs:
  3 BLS requests for 1997–2026, 203 OpenExchangeRates requests for 2010–2026,
  then none).
- A parser fix is applied by rerunning; it reads the saved files. (The first
  real CPI run found the BLS `-`/X marker; the fix was checked by rebuilding
  from the saved responses, with no requests.)
- Every number in a reference table can be traced to the file it came from.
- More code than approach A: a raw store, two parsers, and the registry.
- The data is never committed (`data/` is gitignored). OpenExchangeRates'
  terms allow use but not resale of its data.
