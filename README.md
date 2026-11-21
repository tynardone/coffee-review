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
