# Conventions

How code in this project is organized and named. Decided once here so it isn't
re-decided in every change. If a rule stops fitting, change it here first, with a
[decision record](decisions/) if the reason isn't obvious.

## Packages

```
src/coffee_review/
  cli.py        Typer app: parses arguments, calls pipeline functions. No logic.
  config.py     Settings only.
  models.py     pydantic models: the contracts between stages.
  clients/      Talk to the outside world over HTTP. Return raw text/JSON.
  storage/      Read and write each data layer on disk. Know paths and formats.
  parsers/      Pure functions: raw HTML/XML in, models out. No I/O.
  transforms/   Pure functions on tables: clean, convert, adjust.
  pipeline/     One module per stage: wires clients + storage + parsers/transforms.
```

### The dependency rule

```
cli → pipeline → {clients, storage, parsers, transforms} → {models, config}
```

Imports only point along the arrows. Nothing imports `cli` or `pipeline`.
`parsers` and `transforms` import no I/O at all (no `httpx`, no file access).
That's what lets them be tested with plain inputs, and what stops one stage
reaching into another.

**Only `storage/` knows data formats and paths.** Everything else holds a
storage Protocol (`RawStore`, …) and talks in project terms: slugs, models,
datetimes, strings. One factory (`open_raw_store`) picks the implementation, so
swapping storage is a change in one place ([ADR 0006](decisions/0006-sqlite-manifest-and-storage-boundary.md)).

When it's unclear where new code goes, ask what it *does*:

| It... | It goes in |
|-------|-----------|
| makes an HTTP request | `clients/` |
| reads or writes a file under `data/` | `storage/` |
| talks to the manifest or saved pages | `storage/` (through a Protocol) |
| decides which reviews to fetch | `pipeline/fetch_rules.py` (pure) |
| turns raw HTML/XML into a model | `parsers/` |
| turns one table into another | `transforms/` |
| runs a stage from start to finish | `pipeline/` |
| reads command-line arguments | `cli.py` |

## Data layers

Each stage reads one layer and writes the next. These names are used the same
way in paths, module names and CLI commands.

| Layer | Path | Written by |
|-------|------|-----------|
| raw | `data/raw/` | scrape |
| parsed | `data/parsed/` | parse |
| clean | `data/clean/` | clean |
| reference | `data/reference/` | fx, cpi |
| final | `data/final/` | enrich |

`data/` is never committed.

## Naming

- **Modules are nouns** for what they hold: `review_store.py`, `forex_api.py`.
- **Functions are verbs** for what they do: `fetch_sitemap`, `parse_review`,
  `normalize_price`.
- **One name per concept**, everywhere: *review*, *slug*, *lastmod*, *delisted*.
  Don't alias (no `article` for a review, no `modified` for lastmod).
- **Names say what we observe, not what we assume.** A review missing from the
  sitemap is *delisted*, not "removed" or "deleted": we can't see why it's gone,
  and "removed" reads like we deleted it ourselves.
- **Classes only where there's state to hold**: HTTP clients, stores. Parsing and
  transforming are plain functions.
- **Leading underscore** for module-private helpers (`_parse_retry_after`). Tests
  may import them; other modules may not.
- **Errors end in `Error`** and say what to do: `MissingApiKeyError` names the
  env var to set.

## Tests

- `tests/` mirrors `src/coffee_review/`: `clients/http.py` is tested in
  `tests/clients/test_http.py`.
- Test names read as the behavior: `test_client_error_is_not_retried`, not
  `test_request_2`.
- One behavior per test. If the name needs "and", split it.
- No network in tests. HTTP goes through `httpx.MockTransport`; tests that must
  hit a real service are marked `@pytest.mark.network` and are skipped by default.
- Shared fixtures live in `tests/conftest.py`; saved pages and data files live in
  `tests/fixtures/`, kept small and trimmed to the markup a test needs.
- Write the test first and watch it fail. For code that already exists, break
  the code on purpose once to prove the test catches it.
- **Prefer fakes behind a Protocol over mocks.** A fake is a small working
  implementation (`InMemoryRawStore`, `FakeSite` in `tests/fakes.py`); a mock
  only records calls. Hold a fake to the real thing's contract tests so it
  can't drift.
- Tests may deliberately bypass validation (for example
  `unthrottled_site_settings()` turns the rate limit off); say so in the
  helper's docstring.

## Working

- One concern per commit. A config fix and a parser change are two commits.
- `docs/roadmap.md` is updated in the same commit as the work it tracks.
- `TODO(<milestone>): <what and why>` for known gaps, e.g. `TODO(M1): ...`.
