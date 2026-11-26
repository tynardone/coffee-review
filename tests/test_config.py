"""Tests for coffee_review.config.

The autouse ``clean_env`` fixture (tests/conftest.py) keeps real env vars and
``.env`` out; ``_env_file=None`` below is a second guard for direct ``Settings()``.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from coffee_review.config import (
    CleanSettings,
    CoffeeSiteSettings,
    Settings,
    concurrency_for_rate,
    get_settings,
)


def load_settings() -> Settings:
    return Settings(_env_file=None)


def test_settings_load_with_empty_environment() -> None:
    settings = load_settings()

    assert settings.openexchangerates.api_key is None


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


@pytest.mark.parametrize(
    ("rate", "concurrency", "message"),
    [
        (10.5, None, "10"),  # above the ceiling
        (0, None, "greater than 0"),
        (5, 2, "at least 5"),  # too few workers to reach the rate
        (2, 5, "at most 4"),  # more workers than the rate needs
    ],
)
def test_unreachable_speed_rejected(
    rate: float, concurrency: int | None, message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        CoffeeSiteSettings(rate_per_sec=rate, max_concurrency=concurrency)


def test_concurrency_in_range_accepted() -> None:
    assert CoffeeSiteSettings(rate_per_sec=3, max_concurrency=5).max_concurrency == 5


def test_speed_from_env_follows_same_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COFFEE_REVIEW_COFFEE_SITE__RATE_PER_SEC", "3")

    assert load_settings().coffee_site.max_concurrency == 3


def test_api_key_read_from_env_and_hidden(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COFFEE_REVIEW_OPENEXCHANGERATES__API_KEY", "abc")

    settings = load_settings()

    assert settings.openexchangerates.api_key is not None
    assert settings.openexchangerates.api_key.get_secret_value() == "abc"
    assert "abc" not in repr(settings)


def test_invalid_value_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COFFEE_REVIEW_COFFEE_SITE__MAX_CONCURRENCY", "0")

    with pytest.raises(ValidationError):
        load_settings()


def test_get_settings_ignores_local_env_file() -> None:
    # Tests run from an empty temp directory (tests/conftest.py), so a real
    # .env in the checkout is never read, even through the cached get_settings().
    assert get_settings().openexchangerates.api_key is None


def test_data_dir_default() -> None:
    assert load_settings().data_dir == Path("data")


def test_user_agent_links_the_public_repo() -> None:
    # Polite scraping: site owners can see what this is and where to read about it.
    assert (
        "+https://github.com/tynardone/coffee-review" in CoffeeSiteSettings().user_agent
    )


def test_economic_defaults() -> None:
    economic = load_settings().economic

    assert (economic.fx_start, economic.cpi_start) == (
        date(2010, 1, 1),
        date(1997, 1, 1),
    )
    assert economic.cpi_series == "CUUR0000SA0"


def test_bls_is_keyless_v1() -> None:
    assert str(load_settings().bls.base_url) == "https://api.bls.gov/publicAPI/v1/"


@pytest.mark.parametrize("field", ["FX_START", "CPI_START"])
def test_economic_start_must_be_a_first(
    monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    monkeypatch.setenv(f"COFFEE_REVIEW_ECONOMIC__{field}", "2010-01-15")

    with pytest.raises(ValidationError, match="must be the 1st of a month"):
        load_settings()


def test_roaster_threshold_defaults() -> None:
    clean = load_settings().clean
    assert (
        clean.roaster_merge_at,
        clean.roaster_review_at,
        clean.roaster_review_same_city_at,
    ) == (0.90, 0.85, 0.80)


def test_roaster_thresholds_must_be_ordered() -> None:
    with pytest.raises(ValidationError, match="roaster_review_at"):
        CleanSettings(roaster_merge_at=0.8, roaster_review_at=0.9)


def test_roaster_threshold_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COFFEE_REVIEW_CLEAN__ROASTER_MERGE_AT", "0.95")
    assert load_settings().clean.roaster_merge_at == 0.95
