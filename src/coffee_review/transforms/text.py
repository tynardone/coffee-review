"""The review's text sections, carried through for M8 with one fix.

Some notes contain zero-width characters (invisible, but they break word
matching and lengths). They're removed, and the removal is logged.
"""

from __future__ import annotations

import polars as pl

from coffee_review.transforms.rules import raw, rule_column, rules_when

TEXT_SECTIONS = ("blind_assessment", "notes", "bottom_line")
_ZERO_WIDTH = "[\u200b‌‍﻿]"


def clean_text_sections(frame: pl.LazyFrame) -> pl.LazyFrame:
    """Each text section without zero-width characters."""
    columns = []
    for field in TEXT_SECTIONS:
        text = pl.col(raw(field))
        columns += [
            text.str.replace_all(_ZERO_WIDTH, "").alias(field),
            rules_when(
                (text.str.contains(_ZERO_WIDTH).fill_null(False), "zero_width_removed")
            ).alias(rule_column(field)),
        ]
    return frame.with_columns(columns)
