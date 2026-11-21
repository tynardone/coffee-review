"""Tests for coffee_review.config.

The autouse ``clean_env`` fixture (tests/conftest.py) keeps real env vars and
``.env`` out; ``_env_file=None`` below is a second guard for direct ``Settings()``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from coffee_review.config import (
    CoffeeSiteSettings,
    Settings,
    concurrency_for_rate,
    get_settings,
)


def load_settings() -> Settings:
    return Settings(_env_file=None)


def test_settings_load_with_empty_environment() -> None:
    settings = load_settings()

    assert settings.forex_api.api_key is None


def test_coffee_site_sitemap_url() -> None:
    site = load_settings().coffee_site

    assert str(site.sitemap_url) == "https://www.coffeereview.com/sitemap_index.xml"


# --- scrape speed (ADR 0005) -------------------------------------------------


def test_rate_defaults_to_5_with_concurrency_5() -> None:
    site = CoffeeSiteSettings()

    assert site.rate_per_sec == 5.0
    assert site.max_concurrency == 5


@pytest.mark.parametrize(
    ("rate", "concurrency"), [(0.5, 1), (1, 1), (2.5, 3), (10, 10)]
)
def test_concurrency_worked_out_from_rate(rate: float, concurrency: int) -> None:
    assert concurrency_for_rate(rate) == concurrency
    assert CoffeeSiteSettings(rate_per_sec=rate).max_concurrency == concurrency


def test_rate_above_ceiling_rejected() -> None:
    with pytest.raises(ValidationError, match="10"):
        CoffeeSiteSettings(rate_per_sec=10.5)


def test_zero_rate_rejected() -> None:
    with pytest.raises(ValidationError):
        CoffeeSiteSettings(rate_per_sec=0)


def test_concurrency_too_low_rejected() -> None:
    with pytest.raises(ValidationError, match="at least 5"):
        CoffeeSiteSettings(rate_per_sec=5, max_concurrency=2)


def test_concurrency_too_high_rejected() -> None:
    with pytest.raises(ValidationError, match="at most 4"):
        CoffeeSiteSettings(rate_per_sec=2, max_concurrency=5)


def test_concurrency_in_range_accepted() -> None:
    assert CoffeeSiteSettings(rate_per_sec=3, max_concurrency=5).max_concurrency == 5


def test_speed_from_env_follows_same_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COFFEE_REVIEW_COFFEE_SITE__RATE_PER_SEC", "3")

    assert load_settings().coffee_site.max_concurrency == 3


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
