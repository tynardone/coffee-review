"""The clean report: what a clean run changed, and what needs a person's look.

Built from the clean tables and the cleaning log with Polars (no I/O), then
rendered as Markdown for ``data/clean/report.md``. It answers:

- **What changed:** every rule applied, per field, with examples.
- **What to review:** values the clean stage couldn't place or parse, and close
  spellings it accepted, each marked **new** when the previous run didn't have
  it. This is the list for growing the lookup tables.
- **Coverage and distributions:** a quick check that the results look right.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, NamedTuple

import polars as pl

from coffee_review.models.common import format_elapsed
from coffee_review.pipeline.markdown import cell

if TYPE_CHECKING:
    from collections.abc import Mapping

    from coffee_review.transforms.roasters import NameMap

# Rules that mean "a person should look": the value wasn't understood, or was
# understood by a close spelling that might be wrong.
REVIEW_RULES = (
    "unplaced",
    "unknown_region",
    "unparsed",
    "unknown_currency",
    "ambiguous_currency",
    "unknown_unit",
    "close_spelling",
    "possible_match",
)
# For a close spelling, the clean columns that show what it was placed as.
PLACED_AS: Mapping[str, tuple[str, ...]] = {
    "coffee_origin": ("origin_country",),
    "roaster_location": ("roaster_region", "roaster_country"),
    "price": ("price_unit",),
    "roaster": ("roaster",),
}
DISTRIBUTIONS: Mapping[str, str] = {
    "origin country": "origin_country",
    "growing region": "origin_growing_region",
    "roaster country": "roaster_country",
    "currency": "price_currency",
    "unit": "price_unit",
    "roast level": "roast_level",
}
MAX_EXAMPLES = 3
MAX_DISTRIBUTION = 15


class RuleCount(NamedTuple):
    """How often one rule was applied to one field.

    Attributes:
        field: The field the rule was applied to.
        rule: The rule's name.
        rows: How many rows it was applied to.
        examples: A few ``(slug, raw value)`` pairs to look at.
    """

    field: str
    rule: str
    rows: int
    examples: list[tuple[str, str | None]]  # (slug, raw value)


class ReviewItem(NamedTuple):
    """One value for a person to look at, in the report's *To review* list.

    Attributes:
        field: The field the value is in.
        rule: Why it's listed (unplaced, unknown unit, close spelling, ...).
        raw_value: The value as written.
        rows: How many rows have it.
        placed_as: For a close spelling, what it was matched to.
        new: True when the previous run didn't list it.
    """

    field: str
    rule: str
    raw_value: str | None
    rows: int
    placed_as: str | None  # for close spellings: what it was matched to
    new: bool  # not in the previous run's log


class RoasterNamesSummary(NamedTuple):
    """What the roaster-name step did.

    Attributes:
        spellings: Distinct roaster spellings in the parsed tables.
        roasters: Distinct roaster names after merging.
        merged: Spellings merged into another, by the rule that merged them.
        to_review: Spellings listed as a possible match for a person to decide.
        unused_rows: Spellings in ``roasters.csv`` or ``distinct_roasters.csv``
            that no review uses.
    """

    spellings: int
    roasters: int
    merged: dict[str, int]
    to_review: int
    unused_rows: tuple[str, ...]


@dataclass(frozen=True)
class CleanReport:
    """What one clean run did: totals, rules, things to review, coverage."""

    rows_by_dataset: dict[str, int]
    log_rows: int
    rules: list[RuleCount]
    to_review: list[ReviewItem]
    coverage: dict[str, dict[str, int]]
    distributions: dict[str, list[tuple[str, int]]]
    roaster_names: RoasterNamesSummary | None = None

    @property
    def new_to_review(self) -> dict[str, int]:
        """How many values to review are new this run, by rule."""
        counts: dict[str, int] = {}
        for item in self.to_review:
            if item.new:
                counts[item.rule] = counts.get(item.rule, 0) + 1
        return counts

    def summary_line(self, elapsed_seconds: float) -> str:
        """The one-line summary logged at the end of a clean run."""
        roasted = self.rows_by_dataset.get("roasted", 0)
        green = self.rows_by_dataset.get("green", 0)
        new = sum(self.new_to_review.values())
        return (
            f"clean finished: {roasted + green:,} rows"
            f" · {roasted:,} roasted, {green:,} green · {self.log_rows:,} log rows"
            f" · {len(self.to_review):,} to review ({new:,} new)"
            + (
                f" · {names.roasters:,} roasters from {names.spellings:,} spellings"
                if (names := self.roaster_names)
                else ""
            )
            + f" · in {format_elapsed(elapsed_seconds)}"
        )


def build_clean_report(
    roasted: pl.DataFrame,
    green: pl.DataFrame,
    log: pl.DataFrame,
    previous_log: pl.DataFrame,
    names: NameMap | None = None,
) -> CleanReport:
    """Summarise one clean run.

    Args:
        roasted: The clean roasted table.
        green: The clean green table.
        log: This run's cleaning log.
        previous_log: The last run's log, to mark what's new on the *To review*
            list; empty on a first run.
        names: The roaster name map, for the candidates of a possible match
            and the roaster-names summary.

    Returns:
        The report's contents, ready to render.
    """
    tables = {"roasted": roasted, "green": green}
    return CleanReport(
        rows_by_dataset={name: table.height for name, table in tables.items()},
        log_rows=log.height,
        rules=_rules(log),
        to_review=_to_review(tables, log, previous_log, names),
        coverage={name: _coverage(table) for name, table in tables.items()},
        distributions=_distributions(tables),
        roaster_names=_roaster_names(names) if names else None,
    )


def _roaster_names(names: NameMap) -> RoasterNamesSummary:
    decisions = names.decisions.values()
    return RoasterNamesSummary(
        spellings=len(names.decisions),
        roasters=len({d.name for d in decisions}),
        merged=dict(
            Counter(
                rule for d in decisions for rule in d.rules if rule != "possible_match"
            )
        ),
        to_review=sum("possible_match" in d.rules for d in decisions),
        unused_rows=names.unused_rows,
    )


def _rules(log: pl.DataFrame) -> list[RuleCount]:
    grouped = (
        log.sort("slug")
        .group_by("field", "rule", maintain_order=True)
        .agg(
            pl.len().alias("count"),
            pl.struct("slug", "raw_value").head(MAX_EXAMPLES).alias("examples"),
        )
        .sort(["count", "field", "rule"], descending=[True, False, False])
    )
    return [
        RuleCount(field, rule, count, [(e["slug"], e["raw_value"]) for e in examples])
        for field, rule, count, examples in grouped.iter_rows()
    ]


def _to_review(
    tables: Mapping[str, pl.DataFrame],
    log: pl.DataFrame,
    previous_log: pl.DataFrame,
    names: NameMap | None,
) -> list[ReviewItem]:
    flagged = log.filter(pl.col("rule").is_in(REVIEW_RULES))
    if flagged.is_empty():
        return []
    placed = _placed_as(tables, flagged)
    seen = {
        (row["field"], row["rule"], row["raw_value"])
        for row in previous_log.iter_rows(named=True)
    }
    grouped = (
        flagged.join(placed, on=["slug", "dataset", "field"], how="left")
        .group_by("field", "rule", "raw_value")
        .agg(pl.len().alias("count"), pl.col("placed_as").first())
        .sort(
            ["count", "field", "rule", "raw_value"],
            descending=[True, False, False, False],
        )
    )
    return [
        ReviewItem(
            field,
            rule,
            raw,
            count,
            _candidates(names, raw) if rule == "possible_match" else placed_as,
            _is_new(field, rule, raw, seen, names),
        )
        for field, rule, raw, count, placed_as in grouped.iter_rows()
    ]


def _is_new(
    field: str,
    rule: str,
    raw: str | None,
    seen: set[tuple[str, str, str | None]],
    names: NameMap | None,
) -> bool:
    """True when the previous run didn't list this item.

    A possible match also counts as seen when its pair was listed from the
    other side last run (review counts decide which side carries it).
    """
    if (field, rule, raw) in seen:
        return False
    if rule != "possible_match" or names is None:
        return True
    decision = names.decisions.get(raw or "")
    other_sides = decision.candidates if decision else ()
    return not any((field, rule, other) in seen for other in other_sides)


def _candidates(names: NameMap | None, spelling: str | None) -> str | None:
    """For a possible match: the names it might be, joined with ``; ``."""
    decision = names.decisions.get(spelling or "") if names else None
    return "; ".join(decision.candidates) if decision else None


def _placed_as(
    tables: Mapping[str, pl.DataFrame], flagged: pl.DataFrame
) -> pl.DataFrame:
    """(slug, dataset, field, placed_as): what each close spelling became."""
    parts = []
    for dataset, table in tables.items():
        for field, columns in PLACED_AS.items():
            present = [c for c in columns if c in table.columns]
            if not present:
                continue
            parts.append(
                table.select(
                    "slug",
                    pl.lit(dataset).alias("dataset"),
                    pl.lit(field).alias("field"),
                    pl.concat_str(
                        [pl.col(c).cast(pl.String) for c in present],
                        separator=", ",
                        ignore_nulls=True,
                    ).alias("placed_as"),
                )
            )
    placed = pl.concat(parts)
    close = flagged.filter(pl.col("rule") == "close_spelling").select(
        "slug", "dataset", "field"
    )
    return close.join(
        placed, on=["slug", "dataset", "field"], how="inner"
    ).with_columns(
        pl.when(pl.col("placed_as") != "").then("placed_as").alias("placed_as")
    )


def _coverage(table: pl.DataFrame) -> dict[str, int]:
    counts = table.select(pl.all().count()).row(0)
    return dict(zip(table.columns, counts, strict=True))


def _distributions(
    tables: Mapping[str, pl.DataFrame],
) -> dict[str, list[tuple[str, int]]]:
    distributions: dict[str, list[tuple[str, int]]] = {}
    for name, column in DISTRIBUTIONS.items():
        values = [
            table[column].cast(pl.String)
            for table in tables.values()
            if column in table.columns
        ]
        if not values:
            continue
        counts = (
            pl.concat(values)
            .drop_nulls()
            .value_counts(sort=True)
            .sort(["count", column], descending=[True, False])
            .head(MAX_DISTRIBUTION)
        )
        distributions[name] = [(value, count) for value, count in counts.iter_rows()]
    return distributions


def render_clean_report(report: CleanReport) -> str:
    """The full report as Markdown, one section after another."""
    sections = [
        ["# Clean report"],
        _totals_section(report),
        _rules_section(report),
        _review_section(report),
        _roaster_names_section(report),
        *(_coverage_section(report, dataset) for dataset in report.coverage),
        _distributions_section(report),
    ]
    return "\n\n".join("\n".join(section) for section in sections if section) + "\n"


def _totals_section(report: CleanReport) -> list[str]:
    new = sum(report.new_to_review.values())
    return [
        "## Totals",
        "",
        *(
            f"- {name.title()}: {rows:,} rows"
            for name, rows in report.rows_by_dataset.items()
        ),
        f"- Cleaning-log rows: {report.log_rows:,}",
        (
            f"- To review: {len(report.to_review):,} values"
            f" ({new:,} new since the last run)"
        ),
    ]


def _rules_section(report: CleanReport) -> list[str]:
    lines = ["## Rules applied", ""]
    if not report.rules:
        return [*lines, "None: every value was taken as written."]
    lines += ["| Field | Rule | Rows | Examples |", "|---|---|---|---|"]
    for rule in report.rules:
        examples = ", ".join(
            f"`{cell(raw or '')}` ({slug})" for slug, raw in rule.examples
        )
        lines.append(f"| {rule.field} | {rule.rule} | {rule.rows:,} | {examples} |")
    return lines


def _review_section(report: CleanReport) -> list[str]:
    lines = [
        "## To review",
        "",
        "Values that weren't understood, or were matched by a close spelling.",
        "Fix one by adding its spelling to a lookup table in",
        "`src/coffee_review/transforms/lookups/`, then run `coffee-review clean`.",
        "Roaster items are easiest in `data/clean/roaster_review.csv`: type `same`",
        "or `different` in its `decision` column, then run",
        "`coffee-review roasters apply` and `coffee-review clean`. (By hand: a row",
        "in `roasters.csv` for the same roaster, `distinct_roasters.csv` for",
        "different ones or to undo a wrong close spelling.)",
        "",
    ]
    if not report.to_review:
        return [*lines, "None."]
    lines += [
        "| Field | Rule | Value | Rows | Placed as | |",
        "|---|---|---|---|---|---|",
    ]
    for item in report.to_review:
        lines.append(
            f"| {item.field} | {item.rule} | `{cell(item.raw_value or '')}`"
            f" | {item.rows:,} | {cell(item.placed_as) if item.placed_as else '–'}"
            f" | {'**new**' if item.new else ''} |"
        )
    return lines


def _roaster_names_section(report: CleanReport) -> list[str]:
    names = report.roaster_names
    if names is None:
        return []
    matches = "match" if names.to_review == 1 else "matches"
    summary = (
        f"{names.spellings:,} spellings became {names.roasters:,} roasters;"
        f" {names.to_review:,} possible {matches} to review."
    )
    lines = ["## Roaster names", "", summary]
    if names.merged:
        lines += ["", "| Merged by | Spellings |", "|---|---|"]
        lines += [
            f"| {rule} | {count:,} |"
            for rule, count in sorted(names.merged.items(), key=lambda kv: -kv[1])
        ]
    if names.unused_rows:
        lines += [
            "",
            "Rows in `roasters.csv` or `distinct_roasters.csv` no review uses:",
            "",
            *(f"- `{cell(row)}`" for row in names.unused_rows),
        ]
    return lines


def _coverage_section(report: CleanReport, dataset: str) -> list[str]:
    total = report.rows_by_dataset.get(dataset, 0)
    lines = [
        f"## Coverage: {dataset}",
        "",
        "| Column | Filled | Share |",
        "|---|---|---|",
    ]
    for column, filled in report.coverage[dataset].items():
        share = f"{filled / total:.1%}" if total else "–"
        lines.append(f"| `{column}` | {filled:,} | {share} |")
    return lines


def _distributions_section(report: CleanReport) -> list[str]:
    lines = ["## Distributions"]
    for name, counts in report.distributions.items():
        lines += ["", f"### {name.capitalize()}", "", "| Value | Rows |", "|---|---|"]
        lines += [f"| {cell(value)} | {count:,} |" for value, count in counts]
    return lines
