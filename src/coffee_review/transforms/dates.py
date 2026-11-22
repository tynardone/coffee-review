"""Review and harvest dates.

The site gives a review's date only as a month and year ("May 2020"), so the
clean column is ``review_month``: a date that's always the 1st (Decision 8).
"""

from __future__ import annotations

import polars as pl

from coffee_review.transforms.rules import raw, rule_column, rules_when


def clean_review_month(frame: pl.LazyFrame) -> pl.LazyFrame:
    """``raw_review_date`` "May 2020" → ``review_month`` 2020-05-01."""
    text = pl.col(raw("review_date"))
    month = text.str.strip_chars().str.to_date("%B %Y", strict=False)
    return frame.with_columns(
        month.alias("review_month"),
        rules_when((text.is_not_null() & month.is_null(), "unparsed")).alias(
            rule_column("review_date")
        ),
    )


def clean_harvest(frame: pl.LazyFrame) -> pl.LazyFrame:
    """Green reviews: the harvest's last year, plus the text as written.

    "November 2022 to March 2023" → 2023; the text keeps the range.
    """
    text = pl.col(raw("harvested"))
    year = text.str.extract_all(r"\b\d{4}\b").list.last().cast(pl.Int16)
    return frame.with_columns(
        year.alias("harvest_year"),
        text.alias("harvest_text"),
        rules_when((text.is_not_null() & year.is_null(), "unparsed")).alias(
            rule_column("harvested")
        ),
    )
