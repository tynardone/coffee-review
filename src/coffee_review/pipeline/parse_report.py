"""The parse report: what a parse run found, for people to read.

Built from the records and issues with plain Python (no I/O), then rendered as
Markdown for ``data/parsed/report.md``. It answers three questions:

- **Coverage:** how often each field was found, per dataset.
- **Surprises:** labels and headings the alias table didn't know, and issues.
- **Formats:** the value shapes of each short field (``$99.99/99 aaaaaa``), so
  the clean stage is designed from the formats that really occur.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

from coffee_review.models.common import format_elapsed
from coffee_review.models.parsed import GreenReview, RoastedReview
from coffee_review.parsers.field_aliases import LABEL_ALIASES
from coffee_review.parsers.value_shape import value_shape
from coffee_review.pipeline.markdown import cell

if TYPE_CHECKING:
    from collections.abc import Sequence

    from coffee_review.models.parsed import (
        Dataset,
        IssueKind,
        ParsedReview,
        ParseIssue,
    )

# Short fields worth a shape summary: every table field, plus the score and the
# roaster's website. Long text sections are left out.
SHAPE_FIELDS: tuple[str, ...] = (
    *dict.fromkeys(LABEL_ALIASES.values()),
    "rating",
    "roaster_url",
)
MAX_SHAPES = 10
MAX_EXAMPLES = 3

_COLUMNS: dict[Dataset, tuple[str, ...]] = {
    "roasted": RoastedReview.columns,
    "green": GreenReview.columns,
}

# (shape, how many values had it, example slug, example value)
type ShapeRow = tuple[str, int, str, str]


@dataclass(frozen=True)
class ParseReport:
    """What one parse run found: totals, coverage, unknowns, shapes and issues.

    ``coverage`` counts, per dataset and column, the records where the field
    was found. ``unknown`` maps each ``extra`` key to its page count and up to
    three example slugs. ``shapes`` lists each short field's most common value
    shapes, with an example of each.
    """

    pages: int
    skipped: int
    by_dataset: dict[Dataset, int]
    sold_green: int
    coverage: dict[Dataset, dict[str, int]]
    unknown: dict[str, tuple[int, list[str]]]  # key → (count, example slugs)
    shapes: dict[str, list[ShapeRow]]
    issues_by_kind: dict[IssueKind, int]

    @property
    def issue_count(self) -> int:
        """How many issues the run found, of every kind."""
        return sum(self.issues_by_kind.values())

    def summary_line(self, elapsed_seconds: float) -> str:
        """The one-line summary logged at the end of a parse run."""
        roasted = self.by_dataset.get("roasted", 0)
        green = self.by_dataset.get("green", 0)
        return (
            f"parse finished: {self.pages:,} pages"
            f" · {roasted:,} roasted ({self.sold_green:,} sold green), {green:,} green"
            f" · {self.issue_count:,} issues · {len(self.unknown):,} unknown labels"
            f" · in {format_elapsed(elapsed_seconds)}"
        )


def build_parse_report(
    records: Sequence[ParsedReview],
    issues: Sequence[ParseIssue],
    *,
    pages: int,
    skipped: int,
) -> ParseReport:
    """Summarise one parse run.

    Args:
        records: Every record parsed.
        issues: Every issue found.
        pages: How many reviews the manifest lists.
        skipped: How many of those had no saved copy.

    Returns:
        The report's contents, ready to render.
    """
    in_order = sorted(records, key=lambda r: r.slug)  # stable examples
    return ParseReport(
        pages=pages,
        skipped=skipped,
        by_dataset=dict(Counter(r.dataset for r in in_order)),
        sold_green=sum(isinstance(r, RoastedReview) and r.sold_green for r in in_order),
        coverage=_coverage(in_order),
        unknown=_unknown(in_order),
        shapes=_shapes(in_order),
        issues_by_kind=dict(Counter(issue.kind for issue in issues)),
    )


def _coverage(records: Sequence[ParsedReview]) -> dict[Dataset, dict[str, int]]:
    coverage: dict[Dataset, dict[str, int]] = {
        dataset: dict.fromkeys(columns, 0) for dataset, columns in _COLUMNS.items()
    }
    for record in records:
        counts = coverage[record.dataset]
        for column in counts:
            if getattr(record, column) is not None:
                counts[column] += 1
    return coverage


def _unknown(records: Sequence[ParsedReview]) -> dict[str, tuple[int, list[str]]]:
    counts: Counter[str] = Counter()
    examples: dict[str, list[str]] = {}
    for record in records:
        for key in record.extra:
            counts[key] += 1
            slugs = examples.setdefault(key, [])
            if len(slugs) < MAX_EXAMPLES:
                slugs.append(record.slug)
    return {key: (count, examples[key]) for key, count in counts.most_common()}


def _shapes(records: Sequence[ParsedReview]) -> dict[str, list[ShapeRow]]:
    shapes: dict[str, list[ShapeRow]] = {}
    for field in SHAPE_FIELDS:
        counts: Counter[str] = Counter()
        first: dict[str, tuple[str, str]] = {}
        for record in records:
            value = getattr(record, field, None)
            if value is None:
                continue
            shape = value_shape(value)
            counts[shape] += 1
            first.setdefault(shape, (record.slug, value))
        ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        shapes[field] = [
            (shape, count, *first[shape]) for shape, count in ranked[:MAX_SHAPES]
        ]
    return shapes


def render_report_markdown(report: ParseReport, issues: Sequence[ParseIssue]) -> str:
    """Render the parse report as Markdown, one section after another.

    Args:
        report: The report's contents.
        issues: Every issue, for the issues section.

    Returns:
        The Markdown text.
    """
    sections = [
        ["# Parse report"],
        _totals_section(report),
        *(_coverage_section(report, dataset) for dataset in report.coverage),
        _unknown_section(report),
        _shapes_section(report),
        _issues_section(issues),
    ]
    return "\n\n".join("\n".join(section) for section in sections) + "\n"


def _totals_section(report: ParseReport) -> list[str]:
    kinds = "".join(
        f" · {kind} {n:,}" for kind, n in sorted(report.issues_by_kind.items())
    )
    return [
        "## Totals",
        "",
        f"- Pages read: {report.pages:,} ({report.skipped:,} skipped: no saved copy)",
        (
            f"- Roasted: {report.by_dataset.get('roasted', 0):,}"
            f" ({report.sold_green:,} sold green)"
        ),
        f"- Green: {report.by_dataset.get('green', 0):,}",
        f"- Issues: {report.issue_count:,}{kinds}",
        f"- Unknown labels and headings: {len(report.unknown):,}",
    ]


def _coverage_section(report: ParseReport, dataset: Dataset) -> list[str]:
    total = report.by_dataset.get(dataset, 0)
    lines = [
        f"## Coverage: {dataset}",
        "",
        "| Field | Found | Share |",
        "|---|---|---|",
    ]
    for column, found in report.coverage[dataset].items():
        share = f"{found / total:.1%}" if total else "–"
        lines.append(f"| `{column}` | {found:,} | {share} |")
    return lines


def _unknown_section(report: ParseReport) -> list[str]:
    lines = ["## Unknown labels and headings", ""]
    if not report.unknown:
        return [*lines, "None: every label and heading is in the alias table."]
    lines += ["| Label or heading | Pages | Examples |", "|---|---|---|"]
    for key, (count, examples) in report.unknown.items():
        lines.append(f"| `{cell(key)}` | {count:,} | {', '.join(examples)} |")
    return lines


def _shapes_section(report: ParseReport) -> list[str]:
    lines = ["## Value shapes"]
    for field, rows in report.shapes.items():
        if not rows:
            continue
        lines += [
            "",
            f"### `{field}`",
            "",
            "| Shape | Count | Example |",
            "|---|---|---|",
        ]
        for shape, count, slug, value in rows:
            lines.append(f"| `{cell(shape)}` | {count:,} | `{cell(value)}` ({slug}) |")
    return lines


def _issues_section(issues: Sequence[ParseIssue]) -> list[str]:
    lines = ["## Issues", ""]
    if not issues:
        return [*lines, "None."]
    lines += ["| Slug | Kind | Detail |", "|---|---|---|"]
    for issue in sorted(issues, key=lambda i: (i.kind, i.slug)):
        lines.append(f"| {issue.slug} | {issue.kind} | {cell(issue.detail)} |")
    return lines
