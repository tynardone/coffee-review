# 0003. Build one stage at a time, each finished before the next

Date: 2026-11-20 · Status: accepted

## Context

Each stage has real depth: review pages, for example, use different HTML over
the site's history. The order of work decides whether each stage gets proper
attention and whether the codebase sprawls.

## Decision

Build scrape → parse → clean → reference data → enrich, finishing each stage
before starting the next. Two rules keep this from sprawling:

- Each stage has a written **Done when** in [roadmap.md](../roadmap.md). Work
  stops when it's true; other ideas go to the backlog.
- Each stage's **output contract** (model or schema) is written before the stage
  is built, so the next stage builds against something fixed.

## Why

Each stage gets worked through properly, one concern at a time, and each one
is finished and tested before anything depends on it.

## Alternatives considered

- **Thin end-to-end slice first.** Proves the structure early, but forces a
  shallow first pass over stages with real complexity.
- **Framework first (Dagster).** The framework's conventions would make the
  structural decisions this project exists to learn. Planned as a later stage instead,
  once there's a plain pipeline for it to replace.
