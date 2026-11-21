# 0001. Record decisions

Date: 2026-11-20 · Status: accepted

## Context

v1 of this project sprawled partly because it was hard to keep track of what
had been decided and why. Work is done in small AI-assisted steps, and each new
session (human or AI) needs to know the reasoning, not just the result.

## Decision

Write a short record in `docs/decisions/` whenever a choice is made that a future
reader would wonder about: a tool, a layout, a data format, a rule. Records are
numbered, never deleted, and superseded by a new record when a decision changes.

Each record has: Context, Decision, Why, Alternatives considered.

## Why

The code shows *what*; only a written record shows *why*, and the why is what
stops a decision from being quietly undone later.

## Alternatives considered

- **Commit messages only.** Hard to find later, and too short for reasoning.
- **Comments in code.** Good for local reasons; wrong place for project-wide ones.
