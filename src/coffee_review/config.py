"""Project settings, loaded from environment variables and an optional .env file.


Usage::

    from myproject.config import get_settings
    settings = get_settings()
    settings.weather_api.api_key.get_secret_value()
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import BaseModel, Field, HttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class HTTPClientSettings(BaseModel):
    """Connection, limit and retry settings shared by every client.

    Each service below inherits these and overrides defaults
    as needed.
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

    user_agent: str = "myproject/0.1 (contact: tynardone@gmail.com)"


class CoffeeSiteSettings(HTTPClientSettings):
    base_url: HttpUrl = HttpUrl("www.base")
    sitemap_url: HttpUrl = HttpUrl("www.sitemap")


class ForexApiSettings(HTTPClientSettings):
    api_key: SecretStr


class BLSApiSettings(HTTPClientSettings):
    pass


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MYPROJECT_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bls_api: BLSApiSettings
    forex_api: ForexApiSettings
    coffee_site: CoffeeSiteSettings

    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    """Return the settings, loaded once and cached."""
    return Settings()
