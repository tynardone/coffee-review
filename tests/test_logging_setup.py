"""Tests for coffee_review.logging_setup."""

from __future__ import annotations

import io
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from rich.console import Console

from coffee_review.logging_setup import (
    configure_logging,
    remove_handlers,
    run_log_path,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture(autouse=True)
def _restore_logging() -> Iterator[None]:
    """configure_logging changes process-wide loggers; undo it after each test."""
    httpx_level = logging.getLogger("httpx").level
    yield
    remove_handlers()
    logging.getLogger("httpx").setLevel(httpx_level)


def test_log_file_gets_debug_lines(tmp_path: Path) -> None:
    log_file = tmp_path / "logs" / "run.log"
    configure_logging("INFO", log_file)

    logging.getLogger("coffee_review.pipeline").debug("fetching kenya-karinga-4")

    assert "fetching kenya-karinga-4" in log_file.read_text(encoding="utf-8")


def test_terminal_respects_level(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("WARNING", None)

    logging.getLogger("coffee_review.pipeline").info("quiet")
    logging.getLogger("coffee_review.pipeline").warning("loud")

    err = capsys.readouterr().err
    assert "loud" in err
    assert "quiet" not in err


def test_httpx_logger_quieted() -> None:
    configure_logging("DEBUG", None)

    assert logging.getLogger("httpx").level == logging.WARNING


def test_configure_twice_does_not_duplicate(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO", None)
    configure_logging("INFO", None)

    logging.getLogger("coffee_review").info("once")

    assert capsys.readouterr().err.count("once") == 1


def test_run_log_path_format(tmp_path: Path) -> None:
    now = datetime(2026, 11, 21, 9, 5, 7, tzinfo=UTC)

    assert run_log_path(tmp_path, "scrape", now) == (
        tmp_path / "logs" / "scrape-20261121T090507Z.log"
    )
    assert run_log_path(tmp_path, "parse", now) == (
        tmp_path / "logs" / "parse-20261121T090507Z.log"
    )


def test_log_file_timestamps_are_utc(tmp_path: Path) -> None:
    # Same clock as the file name (run_log_path uses UTC), whatever the
    # machine's local time zone is.
    configure_logging("INFO", tmp_path / "run.log")
    file_handler = next(
        h
        for h in logging.getLogger("coffee_review").handlers
        if isinstance(h, logging.FileHandler)
    )
    record = logging.makeLogRecord(
        {
            "name": "coffee_review.pipeline",
            "levelname": "INFO",
            "msg": "hello",
            "created": datetime(
                2026, 11, 21, 23, 43, 11, 250000, tzinfo=UTC
            ).timestamp(),
            "msecs": 250.0,
        }
    )

    line = file_handler.format(record)

    assert line == "2026-11-21T23:43:11.250Z INFO coffee_review.pipeline: hello"


def test_terminal_lines_go_through_a_rich_console_when_given() -> None:
    out = io.StringIO()
    console = Console(file=out, force_terminal=False, width=200)

    configure_logging("INFO", None, console=console)
    logging.getLogger("coffee_review.pipeline").warning("gone /review/x/: HTTP 404")

    assert out.getvalue().strip() == "gone /review/x/: HTTP 404"
