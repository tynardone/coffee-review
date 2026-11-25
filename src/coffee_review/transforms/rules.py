"""Rule columns, and turning them into the cleaning log.

Every change the clean stage makes is recorded, so a reader can see which rule
changed which value and why: no value silently disappears.

How it works, for one field such as ``agtron``:

- the pipeline renames the parsed column to ``raw_agtron``, so the clean
  columns can reuse field names and the log can quote the original text;
- the transform adds its typed columns (``agtron_whole``, ``agtron_ground``)
  and a ``_rule_agtron`` column: a list of the rules it applied to that row,
  empty when the value was taken as written;
- ``extract_log`` turns every rule column into log rows;
- ``unlogged_nulls`` finds any value that became null with no rule saying why.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl

from coffee_review.models.clean import LOG_SCHEMA

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from coffee_review.models.clean import CleanRule
    from coffee_review.models.parsed import Dataset

RAW_PREFIX = "raw_"
RULE_PREFIX = "_rule_"

# Each parsed field the clean stage interprets → the clean columns it becomes.
# Fields carried through unchanged (slug, title, seller…) aren't listed.
CLEANED_FIELDS: Mapping[str, tuple[str, ...]] = {
    "review_date": ("review_month",),
    "rating": ("rating",),
    "aroma": ("aroma",),
    "acidity": ("acidity",),
    "body": ("body",),
    "flavor": ("flavor",),
    "aftertaste": ("aftertaste",),
    "with_milk": ("with_milk",),
    "roast_level": ("roast_level",),
    "agtron": ("agtron_whole", "agtron_ground"),
    "price": (
        "price_amount",
        "price_currency",
        "price_quantity",
        "price_unit",
        "price_grams",
        "price_note",
    ),
    "coffee_origin": ("origin_country", "origin_region"),
    "roaster_location": ("roaster_city", "roaster_region", "roaster_country"),
    "roaster": ("roaster",),
    "harvested": ("harvest_year", "harvest_text"),
    "lot_size": ("lot_size_kg", "lot_size_text"),
    "blind_assessment": ("blind_assessment",),
    "notes": ("notes",),
    "bottom_line": ("bottom_line",),
}

_UNLOGGED_SCHEMA = pl.Schema({"slug": pl.String(), "field": pl.String()})


def raw(field: str) -> str:
    """The column holding a field's parsed text: ``raw_<field>``."""
    return f"{RAW_PREFIX}{field}"


def rule_column(field: str) -> str:
    """The column listing the rules applied to a field: ``_rule_<field>``."""
    return f"{RULE_PREFIX}{field}"


def rules_when(*pairs: tuple[pl.Expr, CleanRule]) -> pl.Expr:
    """A list of the rules whose condition is true, in the order given.

    ``rules_when((is_zero, "not_measured"), (too_big, "suspect_value"))`` is
    ``["not_measured"]`` for a zero, and ``[]`` for a value neither rule touched.
    """
    named = [pl.when(condition).then(pl.lit(rule)) for condition, rule in pairs]
    return pl.concat_list(named).list.drop_nulls()


def extract_log(frame: pl.DataFrame, dataset: Dataset) -> pl.DataFrame:
    """Turn the rule columns into cleaning-log rows.

    Reads every ``_rule_<field>`` column and quotes the original text from
    ``raw_<field>``.

    Args:
        frame: A cleaned table, with its rule columns.
        dataset: ``roasted`` or ``green``.

    Returns:
        One row per rule applied to a field of a review, sorted.
    """
    parts = [
        frame.select(
            pl.col("slug"),
            pl.lit(dataset).alias("dataset"),
            pl.lit(field).alias("field"),
            pl.col(rule_column(field)).alias("rule"),
            pl.col(raw(field)).alias("raw_value"),
        ).explode("rule")
        for field in _fields_with(frame, rule_column)
    ]
    if not parts:
        return pl.DataFrame(schema=LOG_SCHEMA)
    return (
        pl.concat(parts)
        .filter(pl.col("rule").is_not_null())
        .cast(LOG_SCHEMA)
        .sort("slug", "field", "rule")
    )


def unlogged_nulls(frame: pl.DataFrame) -> pl.DataFrame:
    """(slug, field) for every value that became null with no rule saying why.

    A field counts as lost when its parsed text wasn't null, every clean column
    it feeds is null, and no rule was recorded. Anything found is a transform
    bug, so the pipeline stops the run.
    """
    parts = []
    for field in _fields_with(frame, raw):
        outputs = [c for c in CLEANED_FIELDS[field] if c in frame.columns]
        rules = rule_column(field)
        no_rules = (
            pl.col(rules).list.len() == 0 if rules in frame.columns else pl.lit(True)
        )
        lost = (
            pl.col(raw(field)).is_not_null()
            & pl.all_horizontal(pl.col(c).is_null() for c in outputs)
            & no_rules
        )
        parts.append(frame.filter(lost).select("slug", pl.lit(field).alias("field")))
    if not parts:
        return pl.DataFrame(schema=_UNLOGGED_SCHEMA)
    return pl.concat(parts).sort("slug", "field")


def _fields_with(frame: pl.DataFrame, column: Callable[[str], str]) -> list[str]:
    """The cleaned fields whose ``column(field)`` is in ``frame``."""
    return [f for f in CLEANED_FIELDS if column(f) in frame.columns]
