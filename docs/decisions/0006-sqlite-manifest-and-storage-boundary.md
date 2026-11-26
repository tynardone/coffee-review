# 0006. SQLite for the scrape manifest, behind a storage Protocol

Date: 2026-11-21 · Status: accepted

## Context

The scraper saves about 9,400 review pages, and keeps a manifest recording,
for each review:

- when it was last fetched and attempted;
- whether it's ok, failed or gone;
- whether it has been delisted from the sitemap.

The manifest is **mutable pipeline state**. Its rows are written one at a time
during a run of about 30 minutes, and it must survive a run that's interrupted
or stopped by pushback, and progress should be visible while a scrape is
running.

The scraper should also know nothing about how raw data is stored, so that
the storage can be swapped in one place.

## Decision

- **Saved pages** are gzipped files, one per review:
  `data/raw/reviews/<slug>.html.gz`. Each one is written to a temp file first,
  then moved into place, so a crash can never leave half a page.
- **The manifest** is SQLite in WAL mode: `data/raw/manifest.sqlite`, one row
  per slug. The schema version is kept in `PRAGMA user_version`, and the store
  refuses to open a version it doesn't know.
- **The storage boundary.** The `RawStore` Protocol in `storage/raw_store.py`
  is the only interface the rest of the code sees, and its methods speak only
  in project terms: slugs, models, datetimes and HTML strings.
  - `GzipSqliteRawStore` is the only code that knows about gzip, SQLite or
    paths.
  - `open_raw_store(settings)` is the one place that chooses the
    implementation.
  - Rule, recorded in [conventions.md](../conventions.md): *only `storage/`
    knows data formats and paths*.
- **The in-memory fake** (`InMemoryRawStore`, in `tests/fakes.py`) implements
  the same Protocol. One set of contract tests runs against both stores, so
  the fake can be trusted in the pipeline tests.
- **Later data layers** (parsed, clean, reference, final) will be Parquet,
  queried with DuckDB. DuckDB can also read the SQLite manifest directly.

## Why

- **SQLite suits the workload,** which is many single-row upserts, each
  committed as it happens, during a long run. It's built for exactly this
  (OLTP), and it comes with Python's standard library.
- **WAL mode lets you read while it writes.** Another process can query the
  manifest during a scrape. DuckDB locks its file against other processes
  while one is writing.
- **Pages as separate gzipped files stay easy to inspect.** `zcat` shows any
  page, which matters for surveying HTML variants in M2.
- **The Protocol makes the boundary enforceable, not just a convention:**
  - The pipeline imports no storage details; the plan's grep check confirms
    there's no `sqlite3`, `gzip` or `Path` in `pipeline/scrape.py`.
  - mypy checks that both stores satisfy the Protocol.
  - The contract tests check that they behave the same.

## Alternatives considered

- **A JSON manifest.** Simple to read, but the whole file has to be rewritten
  for every change. Rewriting it after every page risks corrupting it in a
  crash; rewriting it only at the end loses progress when a run stops early.
- **HTML stored inside SQLite.** The most robust single file, but the pages
  are hidden inside a database of a few hundred MB, and checking one means
  writing a query.
- **DuckDB for the manifest.** Excellent for analysis, but it's built for big
  read-heavy queries (OLAP), not row-at-a-time writes, and its file is locked
  against other processes while a scrape writes. It's kept for the later,
  analysis-shaped data layers instead.
- **No Protocol, the pipeline calling the SQLite store directly.** Fewer files,
  but swapping storage would mean editing the pipeline. Tests would also need
  real files or mocks, in place of a trustworthy fake.
