"""The reference layers: saved source responses, and the tidy tables.

- Every response a source answered is kept exactly as received under
  ``data/raw/fx/openexchangerates/`` and ``data/raw/cpi/bls/<series>/``. Those
  files are the source of truth.
- The tidy tables (``data/reference/fx.parquet``, ``cpi.parquet``) are always
  rebuilt from them, never edited.

Only this module knows the paths and formats.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import polars as pl

from coffee_review.models.reference import CpiFile
from coffee_review.storage.atomic import write_atomically, write_parquet

if TYPE_CHECKING:
    from pathlib import Path

    from coffee_review.config import Settings
    from coffee_review.models.reference import ReferenceTable

logger = logging.getLogger(__name__)

FX_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2})\.json$")
CPI_NAME = re.compile(r"^(\d{8}T\d{6}Z)_(\d{4})-(\d{4})\.json$")
STAMP = "%Y%m%dT%H%M%SZ"


class ReferenceLayerMissingError(Exception):
    """There's no reference table to read: fx or cpi hasn't been run here yet."""


def open_reference_store(settings: Settings) -> ReferenceStore:
    """The reference store the pipeline uses."""
    return ReferenceStore(settings.data_dir)


class ReferenceStore:
    """Saved responses under ``<data_dir>/raw/``, tables under
    ``<data_dir>/reference/``.

    ``fx/openexchangerates/<YYYY-MM-DD>.json``: one file per day.
    ``cpi/bls/<series>/<YYYYMMDDTHHMMSSZ>_<start>-<end>.json``: one file per
    request, named by when it was made (UTC) and the years it asked for.
    Files whose names don't fit are skipped with a warning.
    """

    def __init__(self, data_dir: Path) -> None:
        self._fx = data_dir / "raw" / "fx" / "openexchangerates"
        self._cpi = data_dir / "raw" / "cpi" / "bls"
        self._tables = data_dir / "reference"

    # --- Exchange rates

    def save_fx(self, day: date, text: str) -> None:
        """Save one day's response, replacing any saved before for that day."""
        _write_text(self._fx / f"{day:%Y-%m-%d}.json", text)

    def fx_days(self) -> list[date]:
        """Every day with a saved response, oldest first."""
        days: list[date] = []
        for path in _files(self._fx):
            match = FX_NAME.match(path.name)
            day = _parse_day(match.group(1)) if match else None
            if day is None:
                _skip(path)
            else:
                days.append(day)
        return days

    def read_fx(self, day: date) -> str:
        return (self._fx / f"{day:%Y-%m-%d}.json").read_text(encoding="utf-8")

    # --- CPI

    def save_cpi(self, series: str, file: CpiFile, text: str) -> None:
        _write_text(self._cpi_path(series, file), text)

    def cpi_files(self, series: str) -> list[CpiFile]:
        """Every saved response for ``series``, oldest first."""
        found: list[CpiFile] = []
        for path in _files(self._cpi / series):
            match = CPI_NAME.match(path.name)
            fetched_at = _parse_stamp(match.group(1)) if match else None
            if match is None or fetched_at is None:
                _skip(path)
                continue
            found.append(
                CpiFile(
                    fetched_at=fetched_at,
                    start_year=int(match.group(2)),
                    end_year=int(match.group(3)),
                )
            )
        return sorted(found, key=lambda f: f.fetched_at)

    def read_cpi(self, series: str, file: CpiFile) -> str:
        return self._cpi_path(series, file).read_text(encoding="utf-8")

    # --- Tables

    def write_table(self, table: ReferenceTable, frame: pl.DataFrame) -> None:
        write_parquet(frame, self._table_path(table))

    def read_table(self, table: ReferenceTable) -> pl.DataFrame:
        path = self._table_path(table)
        if not path.exists():
            msg = f"no reference data at {path}: run fx or cpi first"
            raise ReferenceLayerMissingError(msg)
        return pl.read_parquet(path)

    def location(self, table: ReferenceTable) -> str:
        return str(self._table_path(table))

    def _cpi_path(self, series: str, file: CpiFile) -> Path:
        stamp = file.fetched_at.astimezone(UTC).strftime(STAMP)
        return self._cpi / series / f"{stamp}_{file.start_year}-{file.end_year}.json"

    def _table_path(self, table: ReferenceTable) -> Path:
        return self._tables / f"{table}.parquet"


def _write_text(path: Path, text: str) -> None:
    write_atomically(path, lambda tmp: tmp.write_text(text, encoding="utf-8"))


def _files(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted(path for path in folder.iterdir() if path.is_file())


def _parse_day(text: str) -> date | None:
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _parse_stamp(text: str) -> datetime | None:
    try:
        return datetime.strptime(text, STAMP).replace(tzinfo=UTC)
    except ValueError:
        return None


def _skip(path: Path) -> None:
    logger.warning("skipping %s: not a saved reference response's name", path)
