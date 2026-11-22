"""The parsed data layer: two datasets of review records, plus issues and the report.

Everything outside ``storage/`` talks to the ``ParsedStore`` Protocol.
``ParquetParsedStore`` is the only code that knows the files, their paths and
their schemas; ``open_parsed_store`` is the one place that picks it (ADR 0006's
rule, the same as for the raw store).

Every run replaces everything: parsing all pages takes seconds, so there's no
incremental writing to get wrong.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import polars as pl

from coffee_review.models.parsed import GreenReview, ParseIssue, RoastedReview

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from coffee_review.config import Settings
    from coffee_review.models.parsed import Dataset, ParsedReview


class ParsedStore(Protocol):
    """Parsed review records for both datasets, the parse issues and the report."""

    def write(
        self,
        records: Sequence[ParsedReview],
        issues: Sequence[ParseIssue],
        report_markdown: str,
    ) -> None:
        """Replace everything from the previous run with these."""
        ...

    def read_reviews(self, dataset: Dataset) -> list[ParsedReview]:
        """One dataset's records, sorted by slug."""
        ...

    def read_issues(self) -> list[ParseIssue]:
        """The issues, sorted by slug and kind."""
        ...

    def report_location(self) -> str:
        """Where the report can be read, for people (a path, say)."""
        ...

    def close(self) -> None:
        """Release any open resources."""
        ...


def open_parsed_store(settings: Settings) -> ParsedStore:
    """The parsed store the pipeline uses."""
    return ParquetParsedStore(settings.data_dir / "parsed")


_MODELS: dict[Dataset, type[RoastedReview | GreenReview]] = {
    "roasted": RoastedReview,
    "green": GreenReview,
}


def _review_schema(dataset: Dataset) -> pl.Schema:
    """Column names and types for one dataset's reviews.parquet (spec order)."""
    columns: dict[str, pl.DataType] = {
        "slug": pl.String(),
        "url": pl.String(),
        "delisted": pl.Boolean(),
    }
    if dataset == "roasted":
        columns["sold_green"] = pl.Boolean()
    columns |= {column: pl.String() for column in _MODELS[dataset].columns}
    columns["extra_count"] = pl.Int32()
    return pl.Schema(columns)


_EXTRA_SCHEMA = pl.Schema(
    {"slug": pl.String(), "label": pl.String(), "value": pl.String()}
)
_ISSUE_SCHEMA = pl.Schema(
    {"slug": pl.String(), "kind": pl.String(), "detail": pl.String()}
)


class ParquetParsedStore:
    """``<root>/{roasted,green}/{reviews,extra_fields}.parquet``,
    ``<root>/parse_issues.parquet`` and ``<root>/report.md``."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def write(
        self,
        records: Sequence[ParsedReview],
        issues: Sequence[ParseIssue],
        report_markdown: str,
    ) -> None:
        for dataset in _MODELS:
            mine = sorted(
                (r for r in records if r.dataset == dataset), key=lambda r: r.slug
            )
            folder = self._root / dataset
            _write_parquet(
                pl.DataFrame(
                    [_review_row(r) for r in mine], schema=_review_schema(dataset)
                ),
                folder / "reviews.parquet",
            )
            extras = [
                {"slug": r.slug, "label": label, "value": value}
                for r in mine
                for label, value in sorted(r.extra.items())
            ]
            _write_parquet(
                pl.DataFrame(extras, schema=_EXTRA_SCHEMA),
                folder / "extra_fields.parquet",
            )
        _write_parquet(
            pl.DataFrame(
                [issue.model_dump() for issue in _sorted_issues(issues)],
                schema=_ISSUE_SCHEMA,
            ),
            self._root / "parse_issues.parquet",
        )
        _write_atomically(
            self._root / "report.md",
            lambda tmp: tmp.write_text(report_markdown, encoding="utf-8"),
        )

    def read_reviews(self, dataset: Dataset) -> list[ParsedReview]:
        folder = self._root / dataset
        extras: dict[str, dict[str, str]] = {}
        for row in pl.read_parquet(folder / "extra_fields.parquet").iter_rows(
            named=True
        ):
            extras.setdefault(row["slug"], {})[row["label"]] = row["value"]
        model = _MODELS[dataset]
        reviews = pl.read_parquet(folder / "reviews.parquet").drop("extra_count")
        return [
            model.model_validate(row | {"extra": extras.get(row["slug"], {})})
            for row in reviews.iter_rows(named=True)
        ]

    def read_issues(self) -> list[ParseIssue]:
        frame = pl.read_parquet(self._root / "parse_issues.parquet")
        return [ParseIssue.model_validate(row) for row in frame.iter_rows(named=True)]

    def report_location(self) -> str:
        return str(self._root / "report.md")

    def close(self) -> None:
        pass


def _review_row(record: ParsedReview) -> dict[str, object]:
    row = record.model_dump(exclude={"dataset", "extra"})
    row["extra_count"] = len(record.extra)
    return row


def _sorted_issues(issues: Sequence[ParseIssue]) -> list[ParseIssue]:
    return sorted(issues, key=lambda i: (i.slug, i.kind, i.detail))


def _write_parquet(frame: pl.DataFrame, path: Path) -> None:
    _write_atomically(path, frame.write_parquet)


def _write_atomically(path: Path, write: Callable[[Path], object]) -> None:
    """Write to a temp file in the same folder, then move it into place.

    A failed write leaves the previous file untouched and no temp file behind.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    os.close(handle)  # reopened by ``write``; mkstemp only reserves a unique name
    tmp = Path(name)
    try:
        write(tmp)
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
