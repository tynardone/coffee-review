"""The overall rating and the 1–10 category scores.

- **Rating:** a whole number, 50–100. ``NR`` means not rated.
- **Scores** (aroma, acidity, body, flavor, aftertaste, with milk): 1–10. Reviews
  from 1997–99 use one decimal place (``6.3``), so scores are kept as decimals.

A number outside its range is kept out of the clean table and logged as
``suspect_value`` (the original text is in the log), so the contract's range
checks only ever catch bugs, not odd data.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl

from coffee_review.transforms.rules import raw, rule_column, rules_when

if TYPE_CHECKING:
    from collections.abc import Sequence

UNKNOWN_MARKERS = ("na", "n/a", "nr")
_NUMBER = r"^\d+(?:\.\d)?$"
_LABELLED = r"^[A-Za-z ]+:\s*(\d+(?:\.\d)?)$"  # "Flavor in milk: 5"
_WORDS = r"^[A-Za-z ]+$"  # acidity "Low", "Very Low", "Moderate"


def clean_rating(frame: pl.LazyFrame) -> pl.LazyFrame:
    """``raw_rating`` "82" → 82; ``NR`` → null (``not_rated``)."""
    text = pl.col(raw("rating")).str.strip_chars()
    number = pl.when(text.str.contains(r"^\d+$")).then(text).cast(pl.Int32)
    in_range = number.is_between(50, 100)
    return frame.with_columns(
        pl.when(in_range).then(number).cast(pl.Int16).alias("rating"),
        rules_when(
            (text.str.to_uppercase() == "NR", "not_rated"),
            (number.is_not_null() & ~in_range, "suspect_value"),
            (text.is_not_null() & number.is_null() & (text != "NR"), "unparsed"),
        ).alias(rule_column("rating")),
    )


def clean_scores(frame: pl.LazyFrame, fields: Sequence[str]) -> pl.LazyFrame:
    """Turn score fields from text into 1–10 decimals.

    Args:
        frame: A table with ``raw_<field>`` for each field.
        fields: The score fields to clean.

    Returns:
        The frame with each score and its rule column added.
    """
    return frame.with_columns(
        expression for field in fields for expression in _score(field)
    )


def _score(field: str) -> tuple[pl.Expr, pl.Expr]:
    text = pl.col(raw(field)).str.strip_chars()
    is_marker = text.str.to_lowercase().is_in(UNKNOWN_MARKERS)
    is_word = ~is_marker & text.str.contains(_WORDS)
    labelled = text.str.extract(_LABELLED, 1)
    number = (pl.when(text.str.contains(_NUMBER)).then(text).otherwise(labelled)).cast(
        pl.Float32
    )
    in_range = number.is_between(1, 10)
    value = pl.when(in_range).then(number).alias(field)
    rules = rules_when(
        (is_marker, "unknown_marker"),
        (is_word, "word_score"),
        (labelled.is_not_null() & in_range, "labelled_score"),
        (number.is_not_null() & ~in_range, "suspect_value"),
        (text.is_not_null() & number.is_null() & ~is_marker & ~is_word, "unparsed"),
    ).alias(rule_column(field))
    return value, rules
