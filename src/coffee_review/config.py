"""Project settings, loaded from environment variables and an optional .env file.

Every section has working defaults, so settings load with an empty environment.
Override any value with ``COFFEE_REVIEW_<SECTION>__<FIELD>``, for example
``COFFEE_REVIEW_COFFEE_SITE__RATE_PER_SEC=0.5``.

Usage::

    from coffee_review.config import get_settings

    settings = get_settings()
    settings.coffee_site.sitemap_url
"""

from __future__ import annotations

import math
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any, Self

from pydantic import (
    BaseModel,
    Field,
    HttpUrl,
    SecretStr,
    ValidationInfo,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

# How the scraper introduces itself: a name, and where site owners can read
# about it (the "+URL" form is the convention crawlers use).
USER_AGENT = "coffee-review/0.1 (+https://github.com/tynardone/coffee-review)"


class HttpClientSettings(BaseModel):
    """Connection, limit and retry settings shared by every client.

    Each service below inherits these and overrides defaults as needed.
    """

    base_url: HttpUrl
    timeout_seconds: float = Field(default=15.0, gt=0)
    connect_timeout_seconds: float = Field(default=5.0, gt=0)

    # Limits
    max_concurrency: int = Field(default=5, ge=1)
    rate_per_sec: float = Field(default=10.0, gt=0)

    # Retries
    max_retries: int = Field(default=5, ge=0)
    backoff_base_seconds: float = Field(default=0.5, gt=0)
    backoff_max_seconds: float = Field(default=30.0, gt=0)

    user_agent: str = USER_AGENT


# Scrape speed (ADR 0005). 5/s is about 31 minutes for a full pass.
DEFAULT_RATE_PER_SEC = 5.0
MAX_RATE_PER_SEC = 10.0


def concurrency_for_rate(rate_per_sec: float) -> int:
    """Concurrency that can sustain ``rate_per_sec``: one second's worth in flight."""
    return max(1, math.ceil(rate_per_sec))


class CoffeeSiteSettings(HttpClientSettings):
    """coffeereview.com.

    Speed is one setting, ``rate_per_sec``; ``max_concurrency`` is worked out
    from it unless given, and a given value must suit the rate. The same rules
    apply whether values come from the CLI or the environment.
    """

    base_url: HttpUrl = HttpUrl("https://www.coffeereview.com/")
    sitemap_url: HttpUrl = HttpUrl("https://www.coffeereview.com/sitemap_index.xml")
    rate_per_sec: float = Field(default=DEFAULT_RATE_PER_SEC, gt=0, le=MAX_RATE_PER_SEC)
    max_concurrency: int = Field(
        default=concurrency_for_rate(DEFAULT_RATE_PER_SEC), ge=1
    )

    @model_validator(mode="before")
    @classmethod
    def _work_out_concurrency(cls, data: Any) -> Any:
        """Fill in max_concurrency from the rate when it wasn't given."""
        if isinstance(data, dict) and data.get("max_concurrency") is None:
            try:
                rate = float(data.get("rate_per_sec", DEFAULT_RATE_PER_SEC))
            except TypeError, ValueError:
                return data  # field validation reports the bad rate
            data = {**data, "max_concurrency": concurrency_for_rate(rate)}
        return data

    @model_validator(mode="after")
    def _check_concurrency_suits_rate(self) -> Self:
        lowest = concurrency_for_rate(self.rate_per_sec)
        highest = 2 * lowest
        rate = f"{self.rate_per_sec:g}/s"
        if self.max_concurrency < lowest:
            raise ValueError(
                f"concurrency {self.max_concurrency} can't reach a rate of {rate}; "
                f"use at least {lowest}, or lower the rate"
            )
        if self.max_concurrency > highest:
            raise ValueError(
                f"concurrency {self.max_concurrency} is more than a rate of {rate} "
                f"can use; use at most {highest}, or raise the rate"
            )
        return self


class OpenExchangeRatesSettings(HttpClientSettings):
    """OpenExchangeRates. The key is only needed by the FX stage."""

    base_url: HttpUrl = HttpUrl("https://openexchangerates.org/api/")
    api_key: SecretStr | None = None


class BlsSettings(HttpClientSettings):
    """Bureau of Labor Statistics public API.

    No key: keyless requests use v1 (25 a day, 10 years each).
    """

    base_url: HttpUrl = HttpUrl("https://api.bls.gov/publicAPI/v1/")
    rate_per_sec: float = Field(default=0.5, gt=0)
    max_concurrency: int = Field(default=1, ge=1)


class EconomicSettings(BaseModel):
    """How far back the economic stages go, and which CPI series."""

    # Exchange rates for the 1st of every month from here (first non-USD
    # price: 2010-03).
    fx_start: date = date(2010, 1, 1)
    # CPI from here, so prices in old review text are covered if ever extracted.
    cpi_start: date = date(1997, 1, 1)
    # CPI-U, all items, US city average, not seasonally adjusted.
    cpi_series: str = "CUUR0000SA0"

    @field_validator("fx_start", "cpi_start")
    @classmethod
    def _first_of_month(cls, value: date, info: ValidationInfo) -> date:
        if value.day != 1:
            raise ValueError(f"{info.field_name} must be the 1st of a month")
        return value


class CleanSettings(BaseModel):
    """How alike two roaster names must be to merge, or to be listed for review.

    Scores are ``fuzz.ratio`` between matching keys, as 0–1 (see
    ``transforms/roasters.py``).
    """

    roaster_merge_at: float = Field(default=0.90, ge=0, le=1)
    roaster_review_at: float = Field(default=0.85, ge=0, le=1)
    roaster_review_same_city_at: float = Field(default=0.80, ge=0, le=1)

    @model_validator(mode="after")
    def _in_order(self) -> Self:
        if not (
            self.roaster_review_same_city_at
            <= self.roaster_review_at
            <= self.roaster_merge_at
        ):
            raise ValueError(
                "roaster thresholds must be in order: roaster_review_same_city_at"
                " <= roaster_review_at <= roaster_merge_at"
            )
        return self


class Settings(BaseSettings):
    """Every setting the project has, loaded from the environment and ``.env``.

    Each section has working defaults; override any field with
    ``COFFEE_REVIEW_<SECTION>__<FIELD>``.
    """

    model_config = SettingsConfigDict(
        env_prefix="COFFEE_REVIEW_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    coffee_site: CoffeeSiteSettings = Field(default_factory=CoffeeSiteSettings)
    openexchangerates: OpenExchangeRatesSettings = Field(
        default_factory=OpenExchangeRatesSettings
    )
    bls: BlsSettings = Field(default_factory=BlsSettings)
    economic: EconomicSettings = Field(default_factory=EconomicSettings)
    clean: CleanSettings = Field(default_factory=CleanSettings)

    # Root of every data layer (raw, parsed, ...) and of run logs.
    data_dir: Path = Path("data")
    log_level: str = "INFO"

    def with_scrape_speed(
        self, rate_per_sec: float, max_concurrency: int | None
    ) -> Self:
        """Return a copy with a new scrape speed, checked by the site's rules.

        Args:
            rate_per_sec: Requests per second.
            max_concurrency: Requests in flight at once; None works it out from the
                rate.

        Returns:
            The new settings.

        Raises:
            pydantic.ValidationError: For a rate, or rate and concurrency pair, the
                rules reject.
        """
        site = CoffeeSiteSettings.model_validate(
            {
                **self.coffee_site.model_dump(
                    exclude={"rate_per_sec", "max_concurrency"}
                ),
                "rate_per_sec": rate_per_sec,
                "max_concurrency": max_concurrency,
            }
        )
        return self.model_copy(update={"coffee_site": site})


@lru_cache
def get_settings() -> Settings:
    """Return the settings, loaded once and cached."""
    return Settings()
