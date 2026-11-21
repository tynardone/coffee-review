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

from functools import lru_cache

from pydantic import BaseModel, Field, HttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# Placeholder until the public repository exists; then point it at the repo URL.
USER_AGENT = "coffee-review/0.1 (personal research project; repository link to follow)"


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


class CoffeeSiteSettings(HttpClientSettings):
    """coffeereview.com. Deliberately slow: about 2.5 hours for a full pass."""

    base_url: HttpUrl = HttpUrl("https://www.coffeereview.com/")
    sitemap_url: HttpUrl = HttpUrl("https://www.coffeereview.com/sitemap_index.xml")
    rate_per_sec: float = Field(default=1.0, gt=0)
    max_concurrency: int = Field(default=2, ge=1)


class ForexApiSettings(HttpClientSettings):
    """OpenExchangeRates. The key is only needed by the FX stage."""

    base_url: HttpUrl = HttpUrl("https://openexchangerates.org/api/")
    api_key: SecretStr | None = None


class BLSApiSettings(HttpClientSettings):
    """Bureau of Labor Statistics public API. No key needed."""

    base_url: HttpUrl = HttpUrl("https://api.bls.gov/publicAPI/v2/")
    rate_per_sec: float = Field(default=0.5, gt=0)
    max_concurrency: int = Field(default=1, ge=1)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="COFFEE_REVIEW_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    coffee_site: CoffeeSiteSettings = Field(default_factory=CoffeeSiteSettings)
    forex_api: ForexApiSettings = Field(default_factory=ForexApiSettings)
    bls_api: BLSApiSettings = Field(default_factory=BLSApiSettings)

    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    """Return the settings, loaded once and cached."""
    return Settings()
