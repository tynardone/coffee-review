"""Writing the hand-kept roaster decisions: the two roaster CSVs.

``roasters.csv`` and ``distinct_roasters.csv`` live with the code in
``transforms/lookups/``, versioned and reviewed like code;
``transforms/lookup_tables.py`` reads them for a clean run. This module is the
only code that writes them, for ``coffee-review roasters apply``, and it only
ever appends rows.
"""

from __future__ import annotations

import csv
import io
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING

from coffee_review.storage.atomic import write_atomically

if TYPE_CHECKING:
    from collections.abc import Sequence

type Row = tuple[str, str]

_LISTED = ("roasters.csv", ("spelling", "roaster"))
_DISTINCT = ("distinct_roasters.csv", ("spelling", "other"))


class RoasterLookups:
    """The two roaster CSVs in one folder.

    Args:
        directory: The folder holding ``roasters.csv`` and
            ``distinct_roasters.csv``.
    """

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def listed_rows(self) -> list[Row]:
        """``roasters.csv``: (spelling, roaster) rows, in file order."""
        return self._read(_LISTED[0])

    def distinct_rows(self) -> list[Row]:
        """``distinct_roasters.csv``: (spelling, other) rows, in file order."""
        return self._read(_DISTINCT[0])

    def add(
        self, listed: Sequence[Row], distinct: Sequence[Row]
    ) -> tuple[list[Row], list[Row]]:
        """Append the rows each file doesn't already have (same text).

        A file with nothing new isn't touched.

        Args:
            listed: Rows for ``roasters.csv``.
            distinct: Rows for ``distinct_roasters.csv``.

        Returns:
            The rows actually added to each file.
        """
        return self._append(*_LISTED, listed), self._append(*_DISTINCT, distinct)

    def _append(self, name: str, header: Row, rows: Sequence[Row]) -> list[Row]:
        existing = self._read(name)
        new = list(dict.fromkeys(row for row in rows if row not in existing))
        if new:
            text = _csv_text([header, *existing, *new])
            write_atomically(
                self._directory / name,
                lambda tmp: tmp.write_text(text, encoding="utf-8"),
            )
        return new

    def _read(self, name: str) -> list[Row]:
        text = (self._directory / name).read_text(encoding="utf-8")
        return [(a, b) for a, b in list(csv.reader(text.splitlines()))[1:]]


def _csv_text(rows: Sequence[Row]) -> str:
    out = io.StringIO()
    csv.writer(out, lineterminator="\n").writerows(rows)
    return out.getvalue()


def open_roaster_lookups() -> RoasterLookups:
    """The roaster lookups the clean stage reads: the package's own files."""
    return RoasterLookups(Path(str(files("coffee_review.transforms.lookups"))))
