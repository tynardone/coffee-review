"""Logging for command-line runs: the terminal, plus an optional run log file.

The terminal shows plain messages at the configured level. The file, when
given, keeps everything at DEBUG with timestamps, so a run can be examined
afterwards (which reviews were fetched and why, every warning).
"""

from __future__ import annotations

import logging
import sys
import time
from typing import TYPE_CHECKING

from rich.logging import RichHandler

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path

    from rich.console import Console

PACKAGE_LOGGER = "coffee_review"
# ISO 8601 in UTC, e.g. 2026-11-21T23:43:11.250Z: the same clock as the log's
# file name and the manifest's timestamps, whatever the machine's time zone.
FILE_FORMAT = "%(asctime)s.%(msecs)03dZ %(levelname)s %(name)s: %(message)s"
FILE_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S"

# Marks handlers this module added, so it can replace them without touching
# anyone else's (pytest's, for example).
_OWNED = "_coffee_review_handler"

# Pass as ``extra=FILE_ONLY`` to log a record to the run log but not the
# terminal: for a message the command already shows the person another way.
FILE_ONLY = {"terminal": False}


def configure_logging(
    level: str, log_file: Path | None, *, console: Console | None = None
) -> None:
    """Send the package's logs to the terminal, and all of them to a file.

    Args:
        level: The lowest level shown in the terminal (``INFO``, ``DEBUG``...).
        log_file: The run log, which gets everything at DEBUG; None for none.
        console: A Rich console to print terminal lines through, so they appear
            above a progress bar drawn on it instead of breaking it.
    """
    logger = logging.getLogger(PACKAGE_LOGGER)
    remove_handlers()
    logger.setLevel(logging.DEBUG)

    terminal: logging.Handler
    if console is None:
        terminal = logging.StreamHandler(sys.stderr)
    else:
        terminal = RichHandler(
            console=console,
            show_time=False,
            show_level=False,
            show_path=False,
            markup=False,
            highlighter=None,
        )
    terminal.setLevel(level.upper())
    terminal.setFormatter(logging.Formatter("%(message)s"))
    terminal.addFilter(lambda record: getattr(record, "terminal", True))
    _add(logger, terminal)

    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(_utc_formatter())
        _add(logger, file_handler)

    # httpx logs every request at INFO; at 5 requests a second that drowns
    # everything else. Its warnings still come through.
    logging.getLogger("httpx").setLevel(logging.WARNING)


def remove_handlers() -> None:
    """Remove (and close) the handlers configure_logging added."""
    logger = logging.getLogger(PACKAGE_LOGGER)
    for handler in [h for h in logger.handlers if getattr(h, _OWNED, False)]:
        logger.removeHandler(handler)
        handler.close()


def run_log_path(data_dir: Path, command: str, now: datetime) -> Path:
    """Work out where a command's run log goes.

    Args:
        data_dir: The root of the data layers.
        command: The command's name (``scrape``, ``parse``...).
        now: When the run started (UTC).

    Returns:
        ``<data_dir>/logs/<command>-<YYYYMMDDTHHMMSSZ>.log``.
    """
    return data_dir / "logs" / f"{command}-{now:%Y%m%dT%H%M%SZ}.log"


def _utc_formatter() -> logging.Formatter:
    formatter = logging.Formatter(FILE_FORMAT, datefmt=FILE_DATE_FORMAT)
    formatter.converter = time.gmtime  # format times in UTC, not local time
    return formatter


def _add(logger: logging.Logger, handler: logging.Handler) -> None:
    setattr(handler, _OWNED, True)
    logger.addHandler(handler)
