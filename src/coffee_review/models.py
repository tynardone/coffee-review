"""The contracts between pipeline stages.

Every model is frozen (immutable once built) and every datetime is
timezone-aware UTC, so values can be passed between stages without surprises.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, ClassVar, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

# WordPress slugs: lowercase letters, digits, hyphens, underscores, and
# percent-encoded characters. This also rules out anything that could act as a
# path when a slug becomes a filename ("..", "a/b", "").
SLUG_PATTERN = r"^[a-z0-9%][a-z0-9%_-]*$"


def require_slug(slug: str) -> str:
    """Return ``slug`` if it's a valid slug; raise ValueError otherwise.

    For code that turns a slug into a file name or key, so a bad one is
    refused before it can act as a path.
    """
    if not re.fullmatch(SLUG_PATTERN, slug):
        raise ValueError(f"not a valid review slug: {slug!r}")
    return slug


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
    delisted_at: UtcDatetime | None


class FetchReason(StrEnum):
    """Why a review is fetched on this run."""

    NEW = "new"
    FAILED = "failed"
    UNKNOWN_TIME = "unknown_time"
    CHANGED = "changed"
    LOOKBACK = "lookback"
    FULL = "full"


def format_elapsed(seconds: float) -> str:
    """``45s``, ``32m 05s`` or ``1h 02m 03s``."""
    total = round(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


class RunMeasurement(_Frozen):
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
        return self.requests / self.elapsed_seconds if self.elapsed_seconds else 0.0

    def describe(self) -> str:
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


class ScrapeSummary(_Frozen):
    """The result of one scrape run."""

    outcome: Literal["finished", "stopped", "error"]
    fetched: dict[FetchReason, int]
    unchanged: int
    failed: int
    gone_slugs: list[str]
    delisted: int
    relisted: int
    remaining: int
    stopped_reason: str | None
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
            line += f" (stopped: {self.stopped_reason}; {self.remaining} remaining)"
        elif self.outcome == "error":
            line += f" (error: {self.stopped_reason})"
        if self.measurement is not None:
            line += "\n" + self.measurement.describe()
        return line


# --- Parsed records (M2) -----------------------------------------------------
#
# Every value is the text as it appears on the page, cleaned of tags and extra
# whitespace; M3 turns it into numbers, dates and so on. A field that isn't on
# the page is None, never a guess.
#
# Roasted and green reviews are different models, so a record can only hold
# the fields its kind of review has: a green review has no aroma, a roasted one
# no lot ID. Unknown fields are refused (extra="forbid"), not silently dropped.

type Dataset = Literal["roasted", "green"]


class _ReviewBase(BaseModel):
    """What every review page has, roasted or green."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    slug: Slug
    url: str
    delisted: bool

    title: str | None = None
    roaster: str | None = None
    roaster_url: str | None = None
    rating: str | None = None
    review_date: str | None = None
    coffee_origin: str | None = None
    price: str | None = None
    blind_assessment: str | None = None
    notes: str | None = None
    bottom_line: str | None = None

    # Labels and headings the alias table doesn't know, or that don't belong to
    # this kind of review, kept as found.
    extra: dict[str, str] = Field(default_factory=dict)


class RoastedReview(_ReviewBase):
    """A roasted coffee review (espresso included: it has ``with_milk``)."""

    dataset: Literal["roasted"] = "roasted"
    # True when the title says the coffee was sold green (1997-1999 reviews).
    sold_green: bool

    roaster_location: str | None = None
    roast_level: str | None = None
    agtron: str | None = None
    aroma: str | None = None
    acidity: str | None = None
    body: str | None = None
    flavor: str | None = None
    aftertaste: str | None = None
    with_milk: str | None = None

    # The dataset's text columns, in table order. A test checks this matches
    # the fields above, so the order is written down exactly once.
    columns: ClassVar[tuple[str, ...]] = (
        "title", "roaster", "roaster_url", "rating", "review_date",
        "roaster_location", "coffee_origin", "roast_level", "agtron", "price",
        "aroma", "acidity", "body", "flavor", "aftertaste", "with_milk",
        "blind_assessment", "notes", "bottom_line",
    )  # fmt: skip


class GreenReview(_ReviewBase):
    """A green (unroasted) coffee review: the site's green-review layout."""

    dataset: Literal["green"] = "green"

    harvested: str | None = None
    seller: str | None = None
    variety: str | None = None
    lot_size: str | None = None
    processing_method: str | None = None
    lot_id: str | None = None

    columns: ClassVar[tuple[str, ...]] = (
        "title", "roaster", "roaster_url", "rating", "review_date",
        "coffee_origin", "price", "harvested", "seller", "variety", "lot_size",
        "processing_method", "lot_id", "blind_assessment", "notes", "bottom_line",
    )  # fmt: skip


# Either kind of review; ``dataset`` says which.
type ParsedReview = Annotated[
    RoastedReview | GreenReview, Field(discriminator="dataset")
]


type IssueKind = Literal[
    "no_review_template", "duplicate_label", "missing_required", "page_missing"
]


class ParseIssue(_Frozen):
    """Something about one page worth a human look. Never stops a run."""

    slug: str
    kind: IssueKind
    detail: str


class ParsedPage(_Frozen):
    """The result of parsing one page: a record (unless there was nothing to
    parse) and any issues found along the way."""

    record: ParsedReview | None
    issues: list[ParseIssue] = Field(default_factory=list)
