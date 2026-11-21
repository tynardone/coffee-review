# 0002. src layout and a one-way dependency rule

Date: 2026-11-20 · Status: accepted

## Context

v1 had no `src/` layout and no rule about which code could call which, so
stages leaked into each other and new code had no obvious home.

## Decision

- Package lives in `src/coffee_review/`.
- Code is split by job: `clients`, `storage`, `parsers`, `transforms`,
  `pipeline`, with `cli`, `config` and `models` alongside.
- Imports flow one way: `cli → pipeline → {clients, storage, parsers, transforms} → {models, config}`.
- `parsers` and `transforms` are pure: no network, no files.
- Stages communicate only through stored data layers (`raw`, `parsed`, `clean`,
  `reference`, `final`) whose shapes are written down as pydantic models or
  table schemas.

Details and a "where does this go?" table: [conventions.md](../conventions.md).

## Why

- `src/` means tests run against the installed package, not whatever happens to
  be in the working directory.
- A one-way rule makes "where does this go?" answerable and makes tangles
  visible in review: an import pointing the wrong way is a bug.
- Pure parsers and transforms can be tested with plain inputs, which is where
  most of this project's tricky logic lives.

## Alternatives considered

- **One module per stage containing everything for that stage.** Fewer files,
  but HTTP, storage and parsing get mixed, which is what made v1 hard to follow.
- **A framework's layout (Dagster assets) from the start.** See 0003.
