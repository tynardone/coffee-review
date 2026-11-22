# coffee-review

A data pipeline that scrapes every review on [coffeereview.com](https://www.coffeereview.com),
parses and cleans it, and converts prices to inflation-adjusted US dollars.
It's a learning and portfolio project: the pipeline's structure and testing are
as much the point as the dataset.

## Pipeline

1. **Scrape**: find reviews through the site's sitemap; download only what's new
   or changed.
2. **Parse**: pull review fields out of the saved HTML.
3. **Clean**: normalize dates, scores, origins and prices into a typed table.
4. **Enrich**: convert prices to USD (OpenExchangeRates) and adjust for
   inflation (BLS CPI-U).

Each stage reads one data layer under `data/` and writes the next, so any stage
can be rerun on its own. Status: see the [roadmap](docs/roadmap.md).

## Setup

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env   # add an OpenExchangeRates key (only the FX stage needs it)
uv run pre-commit install
uv run coffee-review --help
```

## Running the scraper

Start small, then go full:

```bash
uv run coffee-review scrape --limit 20   # try it: fetch 20 pages
uv run coffee-review scrape              # everything new or changed
```

The first full run fetches about 9,400 pages, about 31 minutes at the default
5 requests per second. It's resumable: if it stops for any reason, run it
again and it continues where it left off. Later runs fetch only new and
changed reviews, in seconds.

**Speed.** `--rate R` sets requests per second (at most 10); concurrency is
worked out from it. For experiments, `--rate R --concurrency C` sets both,
within sensible bounds. See [ADR 0005](docs/decisions/0005-scrape-speed.md).

**Reading the result.** Every run ends with two lines, e.g.

```
scrape finished: 12 fetched (3 new, 9 changed), 9,388 unchanged, 0 failed, 0 gone, 1 delisted
  in 14s · 24 requests · avg 1.7/s · avg response 0.48s · rate 5.0/s, concurrency 5
```

If the achieved rate is well below the setting and responses are slow, the
site is struggling: lower `--rate`.

**A broken-looking sitemap.** If more than 25 reviews (and more than 5% of
those saved) seem to have left the sitemap at once, the run refuses to mark
them and exits 1. If you know it's real, rerun with `--allow-delisting`.

**Exit codes.** 0 finished · 1 error · 2 bad options · 3 stopped because the
site pushed back (a 429, 403, challenge page, or 5 failures in a row).

**Logs.** The terminal shows progress; the full DEBUG log of each run is in
`data/logs/scrape-<UTC time>.log`. The manifest can be inspected while a run is
going:

```bash
sqlite3 data/raw/manifest.sqlite "select status, count(*) from reviews group by 1"
```

## Parsing

```bash
uv run coffee-review parse
```

Reads every saved page and writes the parsed layer in about 6 seconds:

- `data/parsed/roasted/` and `data/parsed/green/`: a `reviews.parquet` each
  (one row per review, values as text as they appear on the page) and an
  `extra_fields.parquet` (labels and headings the parser didn't recognise);
- `data/parsed/parse_issues.parquet`: pages worth a look;
- `data/parsed/report.md`: the report.

**Reading the report.** *Coverage* shows how often each field was found.
*Unknown labels and headings* should be empty; anything listed there is a new
name for a field (add it to `parsers/field_aliases.py`) or a new field.
*Value shapes* show the formats each field comes in, with digits as `9` and
words as `a`: the starting point for the clean stage. *Issues* lists pages with
no review template, a missing title/date/rating, or a parser error.

Every run replaces the previous output. See
[ADR 0007](docs/decisions/0007-parse-text-type-later.md) for why values stay
text until the clean stage.

## Checks

```bash
uv run pytest            # tests (add --cov for coverage)
uv run ruff check        # lint
uv run ruff format       # format
uv run mypy              # types
uv run pre-commit run --all-files
```

## Docs

- [Roadmap](docs/roadmap.md): milestones and progress
- [Conventions](docs/conventions.md): structure, naming and test rules
- [Decisions](docs/decisions/): why things are the way they are
- [Design](docs/superpowers/specs/2026-11-20-pipeline-roadmap-design.md): the full roadmap design
