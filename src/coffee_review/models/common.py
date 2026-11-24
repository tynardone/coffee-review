"""Building blocks shared by every stage's contracts.

Slugs, UTC datetimes, the frozen model base, and small formatting helpers.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Annotated

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


class FrozenModel(BaseModel):
    """Base for the contracts: immutable once built."""

    model_config = ConfigDict(frozen=True)


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
