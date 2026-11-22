"""Tests for the coffee-review command line."""

from __future__ import annotations

from importlib.metadata import version
from typing import TYPE_CHECKING, Any

import pytest
from typer.testing import CliRunner

from coffee_review import cli
from coffee_review.cli import app
from coffee_review.models.scrape import ScrapeSummary
from coffee_review.progress_display import RichScrapeProgress

if TYPE_CHECKING:
    from coffee_review.config import Settings

runner = CliRunner()


def test_version_option() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.output.strip() == f"coffee-review {version('coffee-review')}"


def test_help_works_without_env() -> None:
    # clean_env (autouse, tests/conftest.py) guarantees no COFFEE_REVIEW_* vars.
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "coffeereview.com" in result.output


def canned_summary(outcome: str) -> ScrapeSummary:
    return ScrapeSummary.model_validate(
        {
            "outcome": outcome,
            "fetched": {},
            "unchanged": 0,
            "failed": 0,
            "gone_slugs": [],
            "delisted": 0,
            "relisted": 0,
            "remaining": 0,
            "reason": None if outcome == "finished" else "because",
        }
    )


class ScrapeSpy:
    """Stands in for pipeline.scrape: records what the CLI passed, returns a summary."""

    def __init__(self, outcome: str = "finished") -> None:
        self.outcome = outcome
        self.calls: list[dict[str, Any]] = []

    def __call__(self, settings: Settings, **kwargs: Any) -> ScrapeSummary:
        self.calls.append({"settings": settings, **kwargs})
        return canned_summary(self.outcome)


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> ScrapeSpy:
    fake = ScrapeSpy()
    monkeypatch.setattr(cli, "scrape", fake)
    monkeypatch.setattr(cli, "configure_logging", lambda *args, **kwargs: None)
    return fake


@pytest.mark.parametrize(
    ("outcome", "code"),
    [("finished", 0), ("error", 1), ("stopped", 3)],
)
def test_scrape_exit_codes(spy: ScrapeSpy, outcome: str, code: int) -> None:
    spy.outcome = outcome

    assert runner.invoke(app, ["scrape"]).exit_code == code


def test_scrape_passes_options(spy: ScrapeSpy) -> None:
    runner.invoke(app, ["scrape", "--full", "--limit", "5"])

    assert spy.calls[0]["full"] is True
    assert spy.calls[0]["limit"] == 5


def test_scrape_defaults(spy: ScrapeSpy) -> None:
    runner.invoke(app, ["scrape"])

    site = spy.calls[0]["settings"].coffee_site
    assert spy.calls[0]["full"] is False
    assert spy.calls[0]["limit"] is None
    assert (site.rate_per_sec, site.max_concurrency) == (5.0, 5)


def test_scrape_rejects_zero_limit(spy: ScrapeSpy) -> None:
    result = runner.invoke(app, ["scrape", "--limit", "0"])

    assert result.exit_code == 2  # Click's usage-error code
    assert spy.calls == []


def test_scrape_rate_option_sets_rate_and_concurrency(spy: ScrapeSpy) -> None:
    runner.invoke(app, ["scrape", "--rate", "3"])

    site = spy.calls[0]["settings"].coffee_site
    assert (site.rate_per_sec, site.max_concurrency) == (3.0, 3)


def test_scrape_rate_and_concurrency_together(spy: ScrapeSpy) -> None:
    runner.invoke(app, ["scrape", "--rate", "3", "--concurrency", "5"])

    assert spy.calls[0]["settings"].coffee_site.max_concurrency == 5


def test_scrape_concurrency_requires_rate(spy: ScrapeSpy) -> None:
    result = runner.invoke(app, ["scrape", "--concurrency", "4"])

    assert result.exit_code == 2
    assert "--concurrency needs --rate" in result.output
    assert spy.calls == []


def test_scrape_rejects_unreachable_concurrency(spy: ScrapeSpy) -> None:
    result = runner.invoke(app, ["scrape", "--rate", "5", "--concurrency", "2"])

    assert result.exit_code == 2
    assert "use at least 5" in result.output
    assert spy.calls == []


def test_scrape_rejects_rate_above_ceiling(spy: ScrapeSpy) -> None:
    result = runner.invoke(app, ["scrape", "--rate", "50"])

    assert result.exit_code == 2
    assert "10" in result.output
    assert spy.calls == []


def test_scrape_passes_allow_delisting(spy: ScrapeSpy) -> None:
    runner.invoke(app, ["scrape", "--allow-delisting"])

    assert spy.calls[0]["allow_delisting"] is True


def test_scrape_does_not_allow_delisting_by_default(spy: ScrapeSpy) -> None:
    runner.invoke(app, ["scrape"])

    assert spy.calls[0]["allow_delisting"] is False


def test_scrape_draws_a_progress_bar(spy: ScrapeSpy) -> None:
    runner.invoke(app, ["scrape"])

    assert isinstance(spy.calls[0]["progress"], RichScrapeProgress)


def test_scrape_help_keeps_speed_options_together() -> None:
    help_text = runner.invoke(app, ["scrape", "--help"]).output
    order = ["--full", "--limit", "--rate", "--concurrency", "--allow-delisting"]

    positions = [help_text.index(option) for option in order]
    assert positions == sorted(positions)
