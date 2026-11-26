"""Tests for coffee_review.models.scrape: scrape summaries."""

from __future__ import annotations

from coffee_review.models.scrape import (
    FetchReason,
    RunMeasurement,
    ScrapeSummary,
)


def summary(**overrides: object) -> ScrapeSummary:
    fields: dict[str, object] = {
        "outcome": "finished",
        "fetched": {FetchReason.NEW: 3, FetchReason.CHANGED: 9},
        "unchanged": 9388,
        "failed": 0,
        "gone_slugs": [],
        "delisted": 1,
        "relisted": 0,
        "remaining": 0,
        "reason": None,
    }
    fields.update(overrides)
    return ScrapeSummary.model_validate(fields)


def test_describe_finished() -> None:
    assert summary().describe() == (
        "scrape finished: 12 fetched (3 new, 9 changed), 9,388 unchanged, "
        "0 failed, 0 gone, 1 delisted"
    )


def test_describe_stopped() -> None:
    stopped = summary(
        outcome="stopped",
        reason="HTTP 429 at https://…/review/x/",
        remaining=40,
    )

    assert stopped.describe().endswith(
        " (stopped: HTTP 429 at https://…/review/x/; 40 remaining)"
    )


def test_describe_nothing_fetched_has_no_parentheses() -> None:
    assert summary(fetched={}).describe().startswith("scrape finished: 0 fetched, ")


def measurement(**overrides: object) -> RunMeasurement:
    fields: dict[str, object] = {
        "requests": 9431,
        "elapsed_seconds": 1925,
        "mean_response_seconds": 0.52,
        "rate_per_sec": 5.0,
        "max_concurrency": 5,
    }
    fields.update(overrides)
    return RunMeasurement.model_validate(fields)


def test_describe_reports_measurements() -> None:
    lines = summary(measurement=measurement()).describe().splitlines()

    assert lines[1] == (
        "  in 32m 05s · 9,431 requests · avg 4.9/s · avg response 0.52s"
        " · rate 5.0/s, concurrency 5"
    )


def test_describe_without_requests_shows_no_response_time() -> None:
    quiet = measurement(requests=0, elapsed_seconds=0, mean_response_seconds=None)

    assert quiet.describe() == (
        "  in 0s · 0 requests · avg 0.0/s · avg response — · rate 5.0/s, concurrency 5"
    )


def test_summary_of_an_early_ending_needs_only_outcome_and_reason() -> None:
    summary = ScrapeSummary(outcome="error", reason="no review sitemaps listed")

    assert summary.fetched == {}
    assert summary.unchanged == summary.failed == summary.remaining == 0
    assert summary.gone_slugs == []
    assert summary.describe().endswith("(error: no review sitemaps listed)")
