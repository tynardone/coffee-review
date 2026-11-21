"""The contracts between pipeline stages.

Every model is frozen (immutable once built) and every datetime is
timezone-aware UTC, so values can be passed between stages without surprises.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

# WordPress slugs: lowercase letters, digits, hyphens, underscores, and
# percent-encoded characters. This also rules out anything that could act as a
# path when a slug becomes a filename ("..", "a/b", "").
SLUG_PATTERN = r"^[a-z0-9%][a-z0-9%_-]*$"


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC)


UtcDatetime = Annotated[datetime, AfterValidator(_to_utc)]
Slug = Annotated[str, Field(pattern=SLUG_PATTERN)]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class SitemapEntry(_Frozen):
    """One review as listed in the site's sitemap."""

    slug: Slug
    url: str
    lastmod: UtcDatetime | None


class ManifestRecord(_Frozen):
    """What the raw store knows about one review.

    ``fetched_at`` is the last *successful* fetch; ``last_attempt_at`` is the
    last attempt of any outcome.
    """

    slug: Slug
    url: str
    lastmod: UtcDatetime | None
    fetched_at: UtcDatetime | None
    last_attempt_at: UtcDatetime
    status: Literal["ok", "failed", "gone"]
    last_error: str | None
    lookback_done: bool
    first_seen_at: UtcDatetime
    removed_at: UtcDatetime | None


class FetchReason(StrEnum):
    """Why a review is fetched on this run."""

    NEW = "new"
    FAILED = "failed"
    UNKNOWN_TIME = "unknown_time"
    CHANGED = "changed"
    LOOKBACK = "lookback"
    FULL = "full"


class ScrapeSummary(_Frozen):
    """The result of one scrape run."""

    outcome: Literal["finished", "stopped", "error"]
    fetched: dict[FetchReason, int]
    unchanged: int
    failed: int
    gone_slugs: list[str]
    removed: int
    restored: int
    remaining: int
    stopped_reason: str | None

    def describe(self) -> str:
        """One line for the terminal and the log."""
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
            f"{self.removed} removed"
        )
        if self.outcome == "stopped":
            line += f" (stopped: {self.stopped_reason}; {self.remaining} remaining)"
        elif self.outcome == "error":
            line += f" (error: {self.stopped_reason})"
        return line
