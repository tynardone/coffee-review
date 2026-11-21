"""Tests for coffee_review.config.

The autouse ``clean_env`` fixture (tests/conftest.py) keeps real env vars and
``.env`` out; ``_env_file=None`` below is a second guard for direct ``Settings()``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from coffee_review.config import Settings, get_settings


def load_settings() -> Settings:
    return Settings(_env_file=None)


def test_settings_load_with_empty_environment() -> None:
    settings = load_settings()

    assert settings.forex_api.api_key is None


def test_coffee_site_defaults_are_polite() -> None:
    site = load_settings().coffee_site

    assert site.rate_per_sec == 1.0
    assert site.max_concurrency == 2
    assert str(site.sitemap_url) == "https://www.coffeereview.com/sitemap_index.xml"


def test_nested_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COFFEE_REVIEW_COFFEE_SITE__RATE_PER_SEC", "0.25")

    assert load_settings().coffee_site.rate_per_sec == 0.25


def test_api_key_read_from_env_and_hidden(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COFFEE_REVIEW_FOREX_API__API_KEY", "abc")

    settings = load_settings()

    assert settings.forex_api.api_key is not None
    assert settings.forex_api.api_key.get_secret_value() == "abc"
    assert "abc" not in repr(settings)


def test_invalid_value_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COFFEE_REVIEW_COFFEE_SITE__MAX_CONCURRENCY", "0")

    with pytest.raises(ValidationError):
        load_settings()


def test_get_settings_ignores_local_env_file() -> None:
    # Tests run from an empty temp directory (tests/conftest.py), so a real
    # .env in the checkout is never read, even through the cached get_settings().
    assert get_settings().forex_api.api_key is None


def test_data_dir_default() -> None:
    assert load_settings().data_dir == Path("data")
