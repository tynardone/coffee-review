# 0004. Tooling

Date: 2026-11-20 · Status: accepted (later rows confirmed when their milestone starts)

## Context

The project is a portfolio piece and a way to learn current, widely used
open-source tools. Each tool should be the standard for its job, not novel for
its own sake.

## Decision

| Job | Tool | Notes |
|-----|------|-------|
| Packaging, environments | uv | Lockfile is the single source of versions |
| Lint, format | ruff | |
| Type checking | mypy, strict, with the pydantic plugin | Checks `src` and `tests` |
| Tests | pytest, pytest-asyncio, pytest-cov | |
| HTTP mocking | `httpx.MockTransport` | Built in; no extra library |
| Hooks | pre-commit, ruff/mypy as local `uv run` hooks | Same versions as CI |
| CI | GitHub Actions with setup-uv | `uv sync --locked` |
| CLI | Typer | |
| Settings, records | pydantic, pydantic-settings | |
| Snapshot tests (M2) | inline-snapshot | Readable expected output for parsers |
| Property tests (M3) | hypothesis | Edge cases in price/unit parsing |
| HTML parsing (M2) | selectolax | Fast, CSS selectors |
| Tables (M3) | Polars | |
| Table validation (M3) | pandera | Contracts at table boundaries |
| Storage format | Parquet | For parsed, clean, reference, final |
| Querying | DuckDB | Inspection now; dashboard backend later |
| Orchestration (M6) | Dagster | After the plain pipeline works |

## Why

Each is a current default for its job in the Python data ecosystem, and together
they cover the practices this project is meant to demonstrate.

Two choices in detail:

- **pre-commit runs ruff and mypy through `uv run`, not ruff's pre-commit repo.**
  The hooks then use exactly the versions in `uv.lock`, so pre-commit, CI and
  the editor can't disagree.
- **No extra HTTP mocking library.** The clients already accept a `transport`,
  so `httpx.MockTransport` covers it.

## Alternatives considered

- **pandas** instead of Polars: more widespread, but Polars is the modern
  default for new work and is stricter about types.
- **BeautifulSoup** instead of selectolax: more forgiving, slower. Revisit in M2
  if page variants need its leniency.
- **respx** for HTTP mocking: unnecessary given `MockTransport`.
- **SQLite** instead of Parquet + DuckDB: fine for records, weaker for
  analytical queries and the dashboard.
