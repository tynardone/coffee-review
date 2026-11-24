# Architecture

How the code is organised and how data moves through it. The diagrams are
[Mermaid](https://mermaid.js.org/), which GitHub draws in place. Column-level
detail for every table is in the [data dictionary](data-dictionary.md); the
reasons behind the structure are in the [decision records](decisions/).

## The pipeline at a glance

Each stage is one command. It reads one data layer and writes the next, and
never reaches back past the layer it reads.

```mermaid
flowchart LR
    site(["coffeereview.com"])
    apis(["OpenExchangeRates<br>BLS"])
    raw[("data/raw/<br>manifest.sqlite<br>reviews/*.html.gz")]
    parsed[("data/parsed/<br>roasted, green<br>reviews.parquet")]
    clean[("data/clean/<br>roasted, green<br>reviews.parquet<br>cleaning_log.parquet")]
    lookups[["transforms/lookups/*.csv<br>places, currencies, units"]]
    rawref[("data/raw/<br>fx/, cpi/<br>JSON responses")]
    economic[("data/economic/<br>fx, cpi")]
    final[("final table")]

    site -->|"coffee-review scrape"| raw
    raw -->|"coffee-review parse"| parsed
    parsed -->|"coffee-review clean"| clean
    lookups -.->|read by| clean
    clean -.->|"enrich, dbt<br>(planned)"| final
    apis -->|"coffee-review fx / cpi"| rawref
    rawref -->|rebuilt every run| economic
    economic -.->|"(planned)"| final
```

| Layer | Written by | What it holds | Every run |
|---|---|---|---|
| Raw (bronze) | `scrape` | the HTML as fetched, and a manifest of what was fetched when | adds and refreshes pages (incremental) |
| Parsed | `parse` | one row per review, every value as the page's text | replaced in full (≈6 s) |
| Clean (silver) | `clean` | typed columns, a log of every change, a report | replaced in full (≈1 s) |
| Economic | `fx`, `cpi` | exchange rates and US CPI; the responses they came from in `data/raw/` | fetches only what's missing; tables rebuilt in full |
| Marts (gold) | enrich (planned) | joined to exchange rates and inflation | – |

Each stage also writes a Markdown report next to its data
(`data/parsed/report.md`, `data/clean/report.md`) and a run log under
`data/logs/`.

## The dependency tree

Code is split by what it *does*, and imports only point down. This is the
dependency rule ([ADR 0002](decisions/0002-src-layout-and-dependency-rule.md)),
and it isn't just a convention: two **import-linter** contracts in
`pyproject.toml` check it on every commit and in CI (`uv run lint-imports`).

```mermaid
flowchart TD
    cli["<b>cli.py</b><br>arguments → pipeline calls"]

    subgraph runs ["run a stage"]
        pipeline["<b>pipeline/</b><br>scrape · parse · clean<br>+ reports"]
        logging_setup["<b>logging_setup.py</b>"]
        progress["<b>progress_display.py</b>"]
    end

    subgraph workers ["workers: never import each other"]
        clients["<b>clients/</b><br>HTTP only"]
        storage["<b>storage/</b><br>files and formats only"]
        parsers["<b>parsers/</b><br>HTML/XML → models, pure"]
        transforms["<b>transforms/</b><br>tables → tables, pure"]
    end

    subgraph contracts ["shared shapes"]
        models["<b>models/</b><br>pydantic + pandera"]
        config["<b>config.py</b>"]
    end

    cli --> pipeline
    cli --> logging_setup
    cli --> progress
    pipeline --> clients
    pipeline --> storage
    pipeline --> parsers
    pipeline --> transforms
    clients --> models
    storage --> models
    parsers --> models
    transforms --> models
    clients --> config
    storage --> config
    pipeline --> config
```

The two contracts:

1. **Layers.** `cli`, then `pipeline` beside `logging_setup` and
   `progress_display`, then `clients | storage | parsers | transforms`, then
   `models | config`. A module may import from the layers below it, never
   above. Packages on the same layer may not import each other: a parser
   can't reach storage, and a transform can't make an HTTP request.
2. **Pure code does no I/O.** `parsers`, `transforms` and `models` may not
   import `httpx` or `sqlite3`. (Transforms do read their own lookup CSVs,
   which ship with the code, through `importlib.resources`.)

**Only `storage/` knows paths and formats.** The pipeline holds Protocols
(`RawStore`, `ParsedStore`, `CleanStore`) and talks in rows, frames and
models, so a store could move from Parquet to DuckDB by changing one module
and one factory ([ADR 0006](decisions/0006-sqlite-manifest-and-storage-boundary.md)).
Tests run every Protocol against an in-memory fake held to the same contract
tests as the real store.

## Inside each stage

### Scrape

```mermaid
flowchart LR
    sitemaps["sitemap index<br>+ review sitemaps"] -->|clients/coffee_site.py| entries["SitemapEntry<br>(parsers/sitemap.py)"]
    entries --> rules["what to fetch<br>(pipeline/fetch_rules.py)<br>new · failed · changed<br>lookback · --full"]
    manifest[("manifest.sqlite")] --> rules
    rules --> fetch["fetch, rate-limited<br>with retries<br>(clients/http.py)"]
    fetch -->|Fetched| save["save page +<br>manifest row"]
    fetch -->|Failed / Gone| record["record outcome"]
    fetch -->|Pushback| stop(["stop cleanly, exit 3"])
    save --> raw[("data/raw/")]
    record --> raw
```

The scraper is incremental: it fetches only what's new, failed, changed, or
due its one re-fetch the day after it appeared. The speed controls (`--rate`,
`--concurrency`) and why are in [ADR 0005](decisions/0005-scrape-speed.md); the
fetch loop is explained step by step in
[explainers/scrape-pipeline.md](explainers/scrape-pipeline.md).

### Parse

```mermaid
flowchart LR
    raw[("data/raw/")] -->|RawStore| page["one saved page"]
    page --> parser["parsers/review_page.py<br>find every label<br>(parsers/field_aliases.py)"]
    parser -->|record| records["RoastedReview /<br>GreenReview"]
    parser -->|problems| issues["ParseIssue"]
    records --> report["parse report<br>coverage · unknowns ·<br>value shapes"]
    issues --> report
    records -->|ParsedStore| parsed[("data/parsed/")]
    issues --> parsed
    report --> parsed
```

Values stay text ([ADR 0007](decisions/0007-parse-text-type-later.md)). One bad
page never stops the run: it becomes a `ParseIssue`.

### Clean

```mermaid
flowchart TD
    parsed[("data/parsed/")] -->|"ParsedStore.read_table"| rename["rename fields to raw_*"]
    rename --> dates["dates.py<br>review_month, harvest"]
    dates --> scores["scores.py<br>rating, 1–10 scores"]
    scores --> roast["roast.py<br>roast level, Agtron<br>(needs review_month)"]
    roast --> places["places.py<br>roaster location, then origin"]
    places --> price["price.py · units.py<br>(needs roaster_country)"]
    price --> rest["lots.py · text.py"]
    lookups[["lookups/*.csv"]] -.-> places
    lookups -.-> price
    rest --> lost{"any value lost<br>without a rule?"}
    lost -->|yes| stop1(["stop: UnloggedNullsError,<br>nothing written"])
    lost -->|no| log["extract cleaning log<br>(rules.py)"]
    log --> contract{"pandera contract<br>(models/clean.py)"}
    contract -->|breaks| stop2(["stop: CleanContractError,<br>nothing written"])
    contract -->|passes| report["clean report<br>to review, marked new<br>vs the last run's log"]
    report -->|"CleanStore.write"| clean[("data/clean/")]
```

Every transform is `LazyFrame → LazyFrame`. It reads `raw_<field>`, adds typed
columns, and adds `_rule_<field>`, the rules it applied to each row; those
become `cleaning_log.parquet`. Places and prices are parsed once per *distinct*
value in plain Python and joined back. The two checks guard the silver layer:
nothing is written unless every lost value has a rule and both tables meet
their contract ([ADR 0008](decisions/0008-clean-in-polars.md)).

### Economic data

```mermaid
flowchart TD
    saved[("data/raw/fx/openexchangerates/&lt;day&gt;.json<br>data/raw/cpi/bls/&lt;series&gt;/&lt;request&gt;.json")] -->|EconomicStore| parse["parse every saved response"]
    parse --> plan["what's missing?<br>transforms/economic.py<br>(today in UTC, passed in)"]
    plan -->|nothing| rebuild
    plan -->|"missing days / years"| fetch["fetch<br>clients/forex_api.py · bls_api.py"]
    fetch -->|"answered (data or 'no data')"| save["save the response as received"]
    save --> rebuild["rebuild fx / cpi<br>(newest response wins)"]
    fetch -->|"refused, bad key, down"| stop(["stop cleanly, exit 3<br>saved files kept"])
    stop --> rebuild
    rebuild --> contract{"pandera contract<br>(models/economic.py)"}
    contract -->|breaks| stop2(["stop, exit 1,<br>nothing written"])
    contract -->|passes| ref[("data/economic/")]
```

The saved responses are the source of truth; the tables are always rebuilt
from them, so a parser fix needs no requests, and a rerun costs none. Periods a
source will never have, like October 2025's CPI, are saved like any answer and
not asked for again; they're simply absent from the table
([ADR 0009](decisions/0009-keep-raw-economic-responses.md)).

## The lookup tables

The clean stage's interpretation lives in small CSVs, versioned with the code
in `src/coffee_review/transforms/lookups/`. Two are complete lists generated
from ISO standards; the rest are hand-kept and grow from the clean report's
*To review* list.

```mermaid
flowchart LR
    pycountry(["pycountry<br>(dev only)"]) -->|"scripts/generate_lookup_tables.py"| generated
    subgraph generated ["generated"]
        countries["countries.csv"]
        codes["currency_codes.csv"]
    end
    subgraph handkept ["hand-kept"]
        placescsv["places.csv"]
        areas["areas.csv"]
        currencies["currencies.csv"]
        byroaster["roaster_currencies.csv"]
        units["units.csv"]
    end
    countries --> placespy["places.py"]
    placescsv --> placespy
    areas --> placespy
    codes --> pricepy["price.py"]
    currencies --> pricepy
    byroaster --> pricepy
    units --> pricepy
    units --> lots["lots.py"]
    report(["clean report:<br>To review"]) -.->|a person adds a row| handkept
```

## Keeping the docs true

- **The data dictionary is generated** from the models
  (`scripts/generate_data_dictionary.py`); a test fails if it's out of date.
  Each column's meaning is the `description` on the field that defines it.
- **The dependency rule is checked** by import-linter on every commit and in CI.
- **This page is hand-written.** Change it when a stage, package or layer
  changes.
