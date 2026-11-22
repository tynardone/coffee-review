"""The clean (silver) data layer: typed tables, the cleaning log and the report.

Everything outside ``storage/`` talks to the ``CleanStore`` Protocol.
``ParquetCleanStore`` is the only code that knows the files and their paths;
``open_clean_store`` is the one place that picks it (ADR 0006's rule, as for the
raw and parsed stores).

The store doesn't check the tables: the pipeline validates them against the
contract (``models/clean.py``) before it ever calls ``write``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

import polars as pl

from coffee_review.models.clean import LOG_SCHEMA
from coffee_review.storage.atomic import write_atomically, write_parquet

if TYPE_CHECKING:
    from pathlib import Path

    from coffee_review.config import Settings
    from coffee_review.models.clean import CleanTable


class CleanStore(Protocol):
    """The clean roasted and green tables, the cleaning log and the report."""

    def write(
        self,
        roasted: pl.DataFrame,
        green: pl.DataFrame,
        log: pl.DataFrame,
        report_markdown: str,
    ) -> None:
        """Replace everything from the previous run with these."""
        ...

    def read_table(self, table: CleanTable) -> pl.DataFrame:
        """One table as last written.

        Raises ``CleanLayerMissingError`` when nothing has been cleaned yet.
        """
        ...

    def read_previous_log(self) -> pl.DataFrame:
        """The last run's cleaning log, or an empty one on a first run.

        The report compares against it to mark what's new (Decision 10).
        """
        ...

    def report_location(self) -> str:
        """Where the report can be read, for people (a path, say)."""
        ...

    def close(self) -> None:
        """Release any open resources."""
        ...


class CleanLayerMissingError(Exception):
    """There's no clean data to read: clean hasn't been run here yet."""


def open_clean_store(settings: Settings) -> CleanStore:
    """The clean store the pipeline uses."""
    return ParquetCleanStore(settings.data_dir / "clean")


class ParquetCleanStore:
    """``<root>/{roasted,green}/reviews.parquet``, ``<root>/cleaning_log.parquet``
    and ``<root>/report.md``."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def write(
        self,
        roasted: pl.DataFrame,
        green: pl.DataFrame,
        log: pl.DataFrame,
        report_markdown: str,
    ) -> None:
        write_parquet(roasted, self._path("roasted"))
        write_parquet(green, self._path("green"))
        write_parquet(log, self._path("log"))
        write_atomically(
            self._root / "report.md",
            lambda tmp: tmp.write_text(report_markdown, encoding="utf-8"),
        )

    def read_table(self, table: CleanTable) -> pl.DataFrame:
        path = self._path(table)
        if not path.exists():
            msg = f"no clean data at {path}: run clean first"
            raise CleanLayerMissingError(msg)
        return pl.read_parquet(path)

    def read_previous_log(self) -> pl.DataFrame:
        path = self._path("log")
        if not path.exists():
            return pl.DataFrame(schema=LOG_SCHEMA)
        return pl.read_parquet(path)

    def report_location(self) -> str:
        return str(self._root / "report.md")

    def close(self) -> None:
        pass

    def _path(self, table: CleanTable) -> Path:
        if table == "log":
            return self._root / "cleaning_log.parquet"
        return self._root / table / "reviews.parquet"
