"""Tests for the coffee-review command line."""

from __future__ import annotations

from importlib.metadata import version

from typer.testing import CliRunner

from coffee_review.cli import app

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
