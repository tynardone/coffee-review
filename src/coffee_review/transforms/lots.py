"""Green reviews: the size of the lot on offer, in kilograms.

A lot size that starts with a plain weight ("300 kilograms", "1,900 kg (4,189
lbs)") becomes ``lot_size_kg``; anything else ("6 boxes of 76 lbs each", "10 to
20 bags") keeps only its text, logged as ``unparsed``. The text is always kept
as ``lot_size_text``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl

from coffee_review.transforms.rules import raw, rule_column, rules_when

if TYPE_CHECKING:
    from collections.abc import Mapping

    from coffee_review.transforms.lookup_tables import Unit

_WEIGHT_FIRST = r"^\s*(?P<number>\d[\d,]*(?:\.\d+)?)\s*(?P<unit>[A-Za-z.]+)"


def clean_lot_size(frame: pl.LazyFrame, units: Mapping[str, Unit]) -> pl.LazyFrame:
    """``raw_lot_size`` → ``lot_size_kg`` (plain weights only) and ``lot_size_text``."""
    grams_per_unit = {
        spelling: unit.grams for spelling, unit in units.items() if unit.grams
    }
    text = pl.col(raw("lot_size"))
    parts = text.str.extract_groups(_WEIGHT_FIRST)
    number = parts.struct.field("number").str.replace_all(",", "").cast(pl.Float64)
    # One word, so normalise_unit's steps as expressions: case and full stops.
    unit = (
        parts.struct.field("unit")
        .str.to_lowercase()
        .str.replace_all(".", "", literal=True)
    )
    grams = unit.replace_strict(grams_per_unit, default=None, return_dtype=pl.Float64)
    weight = number * grams / 1000
    zero = (weight <= 0).fill_null(False)  # "0 kg": kept out, not a crash
    kg = pl.when(~zero).then(weight)
    not_disclosed = (
        text.str.strip_chars().str.to_lowercase().str.starts_with("not disclosed")
    )
    return frame.with_columns(
        kg.alias("lot_size_kg"),
        text.alias("lot_size_text"),
        rules_when(
            (not_disclosed, "not_disclosed"),
            (zero, "suspect_value"),
            (text.is_not_null() & kg.is_null() & ~not_disclosed & ~zero, "unparsed"),
        ).alias(rule_column("lot_size")),
    )
