"""Tests for the coffee-review command line."""

from __future__ import annotations

import re
from importlib.metadata import version
from typing import TYPE_CHECKING, Any

import polars as pl
import pytest
from typer.testing import CliRunner

from coffee_review import cli
from coffee_review.cli import app
from coffee_review.models.clean import (
    GREEN_SCHEMA,
    LOG_SCHEMA,
    ROASTED_SCHEMA,
    CleanContractError,
)
from coffee_review.models.scrape import ScrapeSummary
from coffee_review.pipeline.clean import CleanRunResult
from coffee_review.pipeline.clean_report import build_clean_report
from coffee_review.pipeline.economic import EconomicRunResult
from coffee_review.pipeline.parse import ParseRunResult
from coffee_review.pipeline.parse_report import build_parse_report
from coffee_review.progress_display import RichRunProgress
from coffee_review.storage.parsed_store import ParsedLayerMissingError
from coffee_review.storage.raw_store import ManifestSchemaError

if TYPE_CHECKING:
    from pathlib import Path

    from coffee_review.config import Settings

# Plain output wherever the tests run: on CI, Rich sees GITHUB_ACTIONS (or
# FORCE_COLOR) and adds colour codes that break the text assertions below.
runner = CliRunner(
    env={"NO_COLOR": "1", "TERM": "dumb", "GITHUB_ACTIONS": None, "FORCE_COLOR": None}
)


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

    progress = spy.calls[0]["progress"]
    assert isinstance(progress, RichRunProgress)
    assert progress.label == "scraping"


def test_scrape_help_keeps_speed_options_together() -> None:
    help_text = runner.invoke(app, ["scrape", "--help"]).output
    order = ["--full", "--limit", "--rate", "--concurrency", "--allow-delisting"]

    positions = [help_text.index(option) for option in order]
    assert positions == sorted(positions)


# --- parse ---------------------------------------------------------------------


class ParseSpy:
    """Stands in for pipeline.parse: records the call, returns a canned result."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def __call__(self, settings: Settings, **kwargs: Any) -> ParseRunResult:
        self.calls.append({"settings": settings, **kwargs})
        if self.error is not None:
            raise self.error
        report = build_parse_report([], [], pages=0, skipped=0)
        return ParseRunResult(
            report=report, elapsed_seconds=1.0, report_location="data/parsed/report.md"
        )


@pytest.fixture
def parse_spy(monkeypatch: pytest.MonkeyPatch) -> ParseSpy:
    fake = ParseSpy()
    monkeypatch.setattr(cli, "parse", fake)
    monkeypatch.setattr(cli, "configure_logging", lambda *args, **kwargs: None)
    return fake


def test_parse_command_exit_code_and_progress(parse_spy: ParseSpy) -> None:
    result = runner.invoke(app, ["parse"])

    assert result.exit_code == 0
    progress = parse_spy.calls[0]["progress"]
    assert isinstance(progress, RichRunProgress)
    assert progress.label == "parsing"


def test_parse_command_prints_report_location(parse_spy: ParseSpy) -> None:
    result = runner.invoke(app, ["parse"])

    assert "report: data/parsed/report.md" in result.output


def test_parse_command_error_exits_1(parse_spy: ParseSpy) -> None:
    parse_spy.error = ManifestSchemaError("manifest schema version 99")

    result = runner.invoke(app, ["parse"])

    assert result.exit_code == 1
    assert "manifest schema version 99" in result.output


def test_help_lists_both_commands() -> None:
    output = runner.invoke(app, ["--help"]).output

    # Command rows, not the words in the app's description.
    assert re.search(r"^\W*scrape\s", output, re.MULTILINE)
    assert re.search(r"^\W*parse\s", output, re.MULTILINE)


class CleanSpy:
    """Stands in for pipeline.clean: records the call, returns a canned result."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[Settings] = []

    def __call__(self, settings: Settings) -> CleanRunResult:
        self.calls.append(settings)
        if self.error is not None:
            raise self.error
        empty = pl.DataFrame(schema=LOG_SCHEMA)
        report = build_clean_report(
            pl.DataFrame(schema=ROASTED_SCHEMA),
            pl.DataFrame(schema=GREEN_SCHEMA),
            empty,
            empty,
        )
        return CleanRunResult(
            report=report, elapsed_seconds=1.0, report_location="data/clean/report.md"
        )


@pytest.fixture
def clean_spy(monkeypatch: pytest.MonkeyPatch) -> CleanSpy:
    fake = CleanSpy()
    monkeypatch.setattr(cli, "clean", fake)
    monkeypatch.setattr(cli, "configure_logging", lambda *args, **kwargs: None)
    return fake


def test_clean_success_prints_report(clean_spy: CleanSpy) -> None:
    result = runner.invoke(app, ["clean"])

    assert result.exit_code == 0
    assert "report: data/clean/report.md" in result.output
    assert len(clean_spy.calls) == 1


def test_clean_missing_parsed_layer(clean_spy: CleanSpy) -> None:
    clean_spy.error = ParsedLayerMissingError(
        "no parsed data at data/parsed/roasted/reviews.parquet: run parse first"
    )

    result = runner.invoke(app, ["clean"])

    assert result.exit_code == 1
    assert "clean failed:" in result.output
    assert "run parse first" in result.output


def test_clean_contract_failure(clean_spy: CleanSpy) -> None:
    clean_spy.error = CleanContractError(
        "clean roasted table broke its contract:\n  rating: less_than_or_equal_to(100)"
    )

    result = runner.invoke(app, ["clean"])

    assert result.exit_code == 1
    assert "rating: less_than_or_equal_to(100)" in result.output


def test_clean_in_help() -> None:
    output = runner.invoke(app, ["--help"]).output

    assert re.search(r"^\W*clean\s", output, re.MULTILINE)


@pytest.mark.parametrize("command", ["parse", "clean"])
def test_error_shown_once(
    command: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fails(*args: object, **kwargs: object) -> None:
        raise ManifestSchemaError("manifest schema version 99")

    monkeypatch.setattr(cli, command, fails)
    monkeypatch.setattr(cli, "run_log_path", lambda *args: tmp_path / "run.log")

    result = runner.invoke(app, [command])

    assert result.output.count("manifest schema version 99") == 1
    assert "manifest schema version 99" in (tmp_path / "run.log").read_text()


# --- fx and cpi


def economic_result(kind: str, outcome: str) -> EconomicRunResult:
    return EconomicRunResult(
        kind=kind,  # type: ignore[arg-type]
        outcome=outcome,  # type: ignore[arg-type]
        reason="openexchangerates: HTTP 429" if outcome == "stopped" else None,
        fetched=1,
        already=0,
        failed=0,
        gaps=(),
        rows=1,
        first=None,
        last=None,
        location="data/economic/fx.parquet",
    )


@pytest.mark.parametrize("kind", ["fx", "cpi"])
@pytest.mark.parametrize(("outcome", "code"), [("finished", 0), ("stopped", 3)])
def test_economic_command_exit_codes(
    monkeypatch: pytest.MonkeyPatch, kind: str, outcome: str, code: int
) -> None:
    calls: list[Settings] = []

    def fake(settings: Settings) -> EconomicRunResult:
        calls.append(settings)
        return economic_result(kind, outcome)

    monkeypatch.setattr(cli, kind, fake)
    monkeypatch.setattr(cli, "configure_logging", lambda *args, **kwargs: None)

    result = runner.invoke(app, [kind])

    assert result.exit_code == code
    assert len(calls) == 1


def test_fx_command_without_key_exits_1_with_the_message() -> None:
    result = runner.invoke(app, ["fx"])

    assert result.exit_code == 1
    assert "fx failed:" in result.output
    assert "COFFEE_REVIEW_FOREX_API__API_KEY" in result.output


@pytest.mark.parametrize("command", ["fx", "cpi"])
def test_economic_commands_in_help(command: str) -> None:
    output = runner.invoke(app, ["--help"]).output

    assert re.search(rf"^\W*{command}\s", output, re.MULTILINE)


@pytest.mark.parametrize("command", ["fx", "cpi"])
def test_bad_start_setting_is_one_clear_line(
    command: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COFFEE_REVIEW_ECONOMIC__FX_START", "2010-01-15")

    result = runner.invoke(app, [command])

    assert result.exit_code == 1
    assert f"{command} failed: fx_start must be the 1st of a month" in result.output
    assert "Traceback" not in result.output
