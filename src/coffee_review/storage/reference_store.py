"""The reference layers: saved source responses, and the tidy tables.

Two stores, each behind a Protocol:

- ``ReferenceRawStore`` keeps every response a source answered, exactly as
  received (``data/raw/fx/...``, ``data/raw/cpi/...``). It's the source of
  truth: the tidy tables are always rebuilt from it, never edited.
- ``ReferenceStore`` holds the tidy tables (``data/reference/*.parquet``).

Only this module knows the paths and formats. ``open_reference_raw_store`` and
``open_reference_store`` are the one place each implementation is picked.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Protocol

import polars as pl

from coffee_review.models.reference import CpiResponse, FxResponse
from coffee_review.storage.atomic import write_atomically, write_parquet

if TYPE_CHECKING:
    from pathlib import Path

    from coffee_review.config import Settings
    from coffee_review.models.reference import ReferenceTable

logger = logging.getLogger(__name__)

FX_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2})\.json$")
CPI_NAME = re.compile(r"^(\d{8}T\d{6}Z)_(\d{4})-(\d{4})\.json$")
STAMP = "%Y%m%dT%H%M%SZ"


class ReferenceRawStore(Protocol):
    """Every response a reference source answered, as received."""

    def save_fx(self, response: FxResponse, text: str) -> None:
        """Save one day's response, replacing any saved before for that day."""
        ...

    def list_fx(self) -> list[FxResponse]:
        """Every saved exchange-rate response, from every source."""
        ...

    def read_fx(self, response: FxResponse) -> str:
        """The saved text of ``response``."""
        ...

    def save_cpi(self, response: CpiResponse, text: str) -> None:
        """Save one CPI response."""
        ...

    def list_cpi(self) -> list[CpiResponse]:
        """Every saved CPI response, from every source and series."""
        ...

    def read_cpi(self, response: CpiResponse) -> str:
        """The saved text of ``response``."""
        ...

    def close(self) -> None:
        """Release any open resources."""
        ...


class ReferenceStore(Protocol):
    """The tidy reference tables, each replaced in full on every write."""

    def write(self, table: ReferenceTable, frame: pl.DataFrame) -> None:
        """Replace ``table`` with ``frame``."""
        ...

    def read_table(self, table: ReferenceTable) -> pl.DataFrame:
        """The table as last written. Raises ReferenceLayerMissingError."""
        ...

    def location(self, table: ReferenceTable) -> str:
        """Where ``table`` lives, for messages."""
        ...

    def close(self) -> None:
        """Release any open resources."""
        ...


class ReferenceLayerMissingError(Exception):
    """There's no reference table to read: fx or cpi hasn't been run here yet."""


def open_reference_raw_store(settings: Settings) -> ReferenceRawStore:
    """The raw reference store the pipeline uses."""
    return FileReferenceRawStore(settings.data_dir / "raw")


def open_reference_store(settings: Settings) -> ReferenceStore:
    """The tidy reference store the pipeline uses."""
    return ParquetReferenceStore(settings.data_dir / "reference")


class FileReferenceRawStore:
    """``<root>/fx/<source>/<YYYY-MM-DD>.json`` and
    ``<root>/cpi/<source>/<series>/<YYYYMMDDTHHMMSSZ>_<start>-<end>.json`` (UTC).

    Files whose names don't fit are skipped with a warning, never guessed at.
    """

    def __init__(self, root: Path) -> None:
        self._root = root

    def save_fx(self, response: FxResponse, text: str) -> None:
        _write_text(self._fx_path(response), text)

    def list_fx(self) -> list[FxResponse]:
        found: list[FxResponse] = []
        for path in _files(self._root / "fx", depth=2):
            match = FX_NAME.match(path.name)
            day = _parse_day(match.group(1)) if match else None
            if day is None:
                _skip(path)
                continue
            found.append(FxResponse(source=path.parent.name, day=day))
        return found

    def read_fx(self, response: FxResponse) -> str:
        return self._fx_path(response).read_text(encoding="utf-8")

    def save_cpi(self, response: CpiResponse, text: str) -> None:
        _write_text(self._cpi_path(response), text)

    def list_cpi(self) -> list[CpiResponse]:
        found: list[CpiResponse] = []
        for path in _files(self._root / "cpi", depth=3):
            match = CPI_NAME.match(path.name)
            fetched_at = _parse_stamp(match.group(1)) if match else None
            if match is None or fetched_at is None:
                _skip(path)
                continue
            found.append(
                CpiResponse(
                    source=path.parent.parent.name,
                    series=path.parent.name,
                    fetched_at=fetched_at,
                    start_year=int(match.group(2)),
                    end_year=int(match.group(3)),
                )
            )
        return found

    def read_cpi(self, response: CpiResponse) -> str:
        return self._cpi_path(response).read_text(encoding="utf-8")

    def close(self) -> None:
        pass

    def _fx_path(self, response: FxResponse) -> Path:
        return self._root / "fx" / response.source / f"{response.day:%Y-%m-%d}.json"

    def _cpi_path(self, response: CpiResponse) -> Path:
        stamp = response.fetched_at.astimezone(UTC).strftime(STAMP)
        name = f"{stamp}_{response.start_year}-{response.end_year}.json"
        return self._root / "cpi" / response.source / response.series / name


class ParquetReferenceStore:
    """``<root>/<table>.parquet`` for each reference table."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def write(self, table: ReferenceTable, frame: pl.DataFrame) -> None:
        write_parquet(frame, self._path(table))

    def read_table(self, table: ReferenceTable) -> pl.DataFrame:
        path = self._path(table)
        if not path.exists():
            msg = f"no reference data at {path}: run fx or cpi first"
            raise ReferenceLayerMissingError(msg)
        return pl.read_parquet(path)

    def location(self, table: ReferenceTable) -> str:
        return str(self._path(table))

    def close(self) -> None:
        pass

    def _path(self, table: ReferenceTable) -> Path:
        return self._root / f"{table}.parquet"


def _write_text(path: Path, text: str) -> None:
    write_atomically(path, lambda tmp: tmp.write_text(text, encoding="utf-8"))


def _files(folder: Path, *, depth: int) -> list[Path]:
    """Files exactly ``depth`` levels below ``folder``, in a stable order."""
    if not folder.is_dir():
        return []
    pattern = "/".join(["*"] * depth)
    return sorted(path for path in folder.glob(pattern) if path.is_file())


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
