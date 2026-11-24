"""The scrape stage's contracts.

Sitemap entries, manifest rows, fetch reasons, and the run summary with its
measurement.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field

from coffee_review.models.common import FrozenModel, Slug, UtcDatetime, format_elapsed


class SitemapEntry(FrozenModel):
    """One review as listed in the site's sitemap."""

    slug: Slug
    url: str
    lastmod: UtcDatetime | None


class ManifestRecord(FrozenModel):
    """What the raw store knows about one review.

    ``fetched_at`` is the last *successful* fetch; ``last_attempt_at`` is the
    last attempt of any outcome.
    """

    slug: Slug = Field(description="The review's id: the last part of its URL.")
    url: str = Field(description="The review page's address.")
    lastmod: UtcDatetime | None = Field(
        description="When the sitemap last said the page changed (UTC), if it said."
    )
    fetched_at: UtcDatetime | None = Field(
        description="The last successful fetch of the page (UTC); empty if never."
    )
    last_attempt_at: UtcDatetime = Field(
        description="The last fetch attempt of any outcome (UTC)."
    )
    status: Literal["ok", "failed", "gone"] = Field(
        description=(
            "How the last attempt went: ok, failed, or gone (the site says the page no"
            " longer exists)."
        )
    )
    last_error: str | None = Field(
        description="What went wrong on the last failed attempt."
    )
    lookback_done: bool = Field(
        description=(
            "Whether the page has had its one re-fetch a day after it appeared "
            "(reviews are often edited soon after publishing)."
        )
    )
    first_seen_at: UtcDatetime = Field(
        description="When this review first appeared in a sitemap (UTC)."
    )
    delisted_at: UtcDatetime | None = Field(
        description=(
            "When the review dropped out of the sitemaps (UTC); its saved copy is kept."
        )
    )


class FetchReason(StrEnum):
    """Why a review is fetched on this run."""

    NEW = "new"
    FAILED = "failed"
    UNKNOWN_TIME = "unknown_time"
    CHANGED = "changed"
    LOOKBACK = "lookback"
    FULL = "full"


class RunMeasurement(FrozenModel):
    """What a run actually did to the site, so speeds can be compared (ADR 0005).

    ``requests`` counts every HTTP attempt: sitemaps, pages and retries.
    ``mean_response_seconds`` times the request itself, not the wait for a
    rate-limit slot; it is None when nothing was sent.
    """

    requests: int
    elapsed_seconds: float
    mean_response_seconds: float | None
    rate_per_sec: float
    max_concurrency: int

    @property
    def achieved_rate(self) -> float:
        """Requests per second actually achieved over the whole run."""
        return self.requests / self.elapsed_seconds if self.elapsed_seconds else 0.0

    def describe(self) -> str:
        """The measurement line printed under the scrape summary."""
        response = (
            f"{self.mean_response_seconds:.2f}s"
            if self.mean_response_seconds is not None
            else "—"
        )
        return (
            f"  in {format_elapsed(self.elapsed_seconds)}"
            f" · {self.requests:,} requests"
            f" · avg {self.achieved_rate:.1f}/s"
            f" · avg response {response}"
            f" · rate {self.rate_per_sec:.1f}/s, concurrency {self.max_concurrency}"
        )


class ScrapeSummary(FrozenModel):
    """The result of one scrape run."""

    outcome: Literal["finished", "stopped", "error"]
    # Why the run stopped or failed; None when it finished.
    reason: str | None = None
    # The counts default to zero, so a run that ended before fetching anything
    # is just ScrapeSummary(outcome=..., reason=...).
    fetched: dict[FetchReason, int] = Field(default_factory=dict)
    unchanged: int = 0
    failed: int = 0
    gone_slugs: list[str] = Field(default_factory=list)
    delisted: int = 0
    relisted: int = 0
    remaining: int = 0
    measurement: RunMeasurement | None = None

    def describe(self) -> str:
        """The summary line, plus the measurement line once it's attached."""
        total = sum(self.fetched.values())
        reasons = ", ".join(
            f"{self.fetched[reason]} {reason}"
            for reason in FetchReason
            if self.fetched.get(reason)
        )
        fetched = f"{total} fetched" + (f" ({reasons})" if reasons else "")
        line = (
            f"scrape {self.outcome}: {fetched}, {self.unchanged:,} unchanged, "
            f"{self.failed} failed, {len(self.gone_slugs)} gone, "
            f"{self.delisted} delisted"
        )
        if self.outcome == "stopped":
            line += f" (stopped: {self.reason}; {self.remaining} remaining)"
        elif self.outcome == "error":
            line += f" (error: {self.reason})"
        if self.measurement is not None:
            line += "\n" + self.measurement.describe()
        return line
