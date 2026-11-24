"""Roast level, and the two Agtron readings.

Agtron (see docs/agtron.md) measures roast colour: the higher the number, the
lighter the roast. Coffee Review prints two readings, whole bean then ground
(``55/68``). Ground coffee is lighter inside, so its number is usually higher.

The rules:

- **Unknown:** a blank side, ``NA`` or ``na``; ``0`` means not measured; ``g`` and
  ``wb`` are the template's own label ("ground/whole bean") printed where the
  numbers should be.
- **Typos with a known pattern are corrected and flagged** (``agtron_corrected``,
  rule ``typo_corrected``). A reading above 150 is corrected to its first two
  digits only when one of these explains the extra digits:

  - the other reading typed onto its end: ``6489/89`` → 64;
  - the other reading's first digit slipped on: ``547/74`` → 54;
  - the number typed twice: ``5252/70`` → 52;
  - its last digit doubled: ``555/73`` → 55;

  and only if the corrected pair then looks normal (see ``_looks_normal``).
  Any other doubtful reading (``48/689``, a decimal) is kept as written and
  flagged ``agtron_suspect``.
- **June 2002 – March 2003, the readings were printed the other way round:**
  a row in that window whose ground reading is darker (lower) than its whole
  bean is swapped back, with ``agtron_swapped``. A row in the window that
  doesn't look reversed is flagged suspect, since the template said otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import polars as pl

from coffee_review.models.clean import ROAST_LEVELS, RoastLevel
from coffee_review.transforms.rules import raw, rule_column, rules_when

SWAP_FIRST_MONTH = date(2002, 6, 1)
SWAP_LAST_MONTH = date(2003, 3, 1)
SUSPECT_ABOVE = 150
CONTRACT_MAX = 7000  # beyond this, a reading can't be stored (models/clean.py)

_UNKNOWN_MARKERS = ("na", "n/a")
_LEVELS_BY_LOWER = {level.lower(): level for level in ROAST_LEVELS}


def clean_roast_level(frame: pl.LazyFrame) -> pl.LazyFrame:
    """``raw_roast_level`` to one of the six levels, in any case."""
    lower = pl.col(raw("roast_level")).str.strip_chars().str.to_lowercase()
    level = lower.replace_strict(
        _LEVELS_BY_LOWER, default=None, return_dtype=RoastLevel
    )
    is_marker = lower.is_in(_UNKNOWN_MARKERS)
    return frame.with_columns(
        level.alias("roast_level"),
        rules_when(
            (is_marker, "unknown_marker"),
            (lower.is_not_null() & level.is_null() & ~is_marker, "unparsed"),
        ).alias(rule_column("roast_level")),
    )


@dataclass(frozen=True)
class _Side:
    """One side of an Agtron reading (whole bean or ground), as expressions."""

    digits: pl.Expr  # the text when it's a whole number, else null
    value: pl.Expr  # the reading as written, or null
    unknown: pl.Expr  # blank or NA
    not_measured: pl.Expr  # 0
    label: pl.Expr  # g or wb
    unparsed: pl.Expr  # anything else that isn't a number
    suspect: pl.Expr  # a decimal, or above SUSPECT_ABOVE


def _side(text: pl.Expr) -> _Side:
    text = text.str.strip_chars()
    lower = text.str.to_lowercase()
    is_number = text.str.contains(r"^\d+(?:\.\d+)?$")
    number = pl.when(is_number).then(text).cast(pl.Float64)
    # Tested after rounding: 0.4 rounds to 0, which can't be stored either.
    usable = number.round(0).is_between(0, CONTRACT_MAX, closed="right")
    unknown = (text == "") | lower.is_in(_UNKNOWN_MARKERS)
    label = lower.is_in(("g", "wb"))
    return _Side(
        digits=pl.when(text.str.contains(r"^\d+$")).then(text),
        value=pl.when(usable).then(number.round(0)).cast(pl.Int16),
        unknown=unknown,
        not_measured=number == 0,
        label=label,
        unparsed=~is_number & ~unknown & ~label,
        suspect=(number > 0) & ((number > SUSPECT_ABOVE) | (number != number.floor())),
    )


def _typo_pattern(side: _Side, other: _Side) -> pl.Expr:
    """True when a known typing slip explains a reading's extra digits.

    Each pattern leaves the first two digits as the real reading.
    """
    text, length = side.digits, side.digits.str.len_chars()
    third = text.str.slice(2, 1)
    other_appended = (length > 2) & (text.str.slice(2) == other.digits)
    first_digit_slipped = (length == 3) & (third == other.digits.str.slice(0, 1))
    typed_twice = (length == 4) & (text.str.slice(2) == text.str.slice(0, 2))
    last_doubled = (length == 3) & (third == text.str.slice(1, 1))
    explained = other_appended | first_digit_slipped | typed_twice | last_doubled
    return (text.cast(pl.Int64) > SUSPECT_ABOVE) & explained  # as written


def _looks_normal(first: pl.Expr, second: pl.Expr, reversed_print: pl.Expr) -> pl.Expr:
    """A corrected pair must read like a real one, in the order it was printed.

    Ground is lighter (higher) than whole bean, so normally first ≤ second; in
    the swap window the site printed ground first, so there first ≥ second.
    """
    return pl.when(reversed_print).then(first >= second).otherwise(first <= second)


def clean_agtron(frame: pl.LazyFrame) -> pl.LazyFrame:
    """``raw_agtron`` "55/68" to whole and ground readings, with flags and rules.

    Needs ``review_month`` (from ``clean_review_month``) for the swap window.
    """
    text = pl.col(raw("agtron"))
    has_slash = text.str.contains("/").fill_null(False)
    halves = text.str.split_exact("/", 1)
    whole = _side(halves.struct.field("field_0"))
    ground = _side(halves.struct.field("field_1"))
    in_swap_months = (
        pl.col("review_month")
        .is_between(SWAP_FIRST_MONTH, SWAP_LAST_MONTH)
        .fill_null(False)
    )

    def either(on_whole: pl.Expr, on_ground: pl.Expr) -> pl.Expr:
        """True when a condition holds on either side of a slashed reading."""
        return has_slash & (on_whole | on_ground).fill_null(False)

    # 1. Typos: correct a side to its first two digits when a pattern explains it.
    whole_fix = whole.digits.str.slice(0, 2).cast(pl.Int16)
    ground_fix = ground.digits.str.slice(0, 2).cast(pl.Int16)
    fix_whole = (
        _typo_pattern(whole, ground)
        & _looks_normal(whole_fix, ground.value, in_swap_months)
    ).fill_null(False)
    fix_ground = (
        _typo_pattern(ground, whole)
        & _looks_normal(whole.value, ground_fix, in_swap_months)
    ).fill_null(False)
    whole_value = pl.when(fix_whole).then(whole_fix).otherwise(whole.value)
    ground_value = pl.when(fix_ground).then(ground_fix).otherwise(ground.value)
    corrected = either(fix_whole, fix_ground)
    suspect = either(whole.suspect & ~fix_whole, ground.suspect & ~fix_ground)

    # 2. The swap window: put readings printed ground-first back in order.
    in_window = (
        in_swap_months & whole_value.is_not_null() & ground_value.is_not_null()
    ).fill_null(False)
    swap = in_window & (ground_value < whole_value)
    unswapped = in_window & ~swap
    whole_value = pl.when(has_slash).then(whole_value)
    ground_value = pl.when(has_slash).then(ground_value)

    return frame.with_columns(
        pl.when(swap).then(ground_value).otherwise(whole_value).alias("agtron_whole"),
        pl.when(swap).then(whole_value).otherwise(ground_value).alias("agtron_ground"),
        swap.alias("agtron_swapped"),
        corrected.alias("agtron_corrected"),
        (suspect | unswapped).alias("agtron_suspect"),
        rules_when(
            (either(whole.unknown, ground.unknown), "unknown_marker"),
            (either(whole.not_measured, ground.not_measured), "not_measured"),
            (either(whole.label, ground.label), "template_label"),
            (
                either(whole.unparsed, ground.unparsed)
                | (text.is_not_null() & ~has_slash),
                "unparsed",
            ),
            (suspect, "suspect_value"),
            (corrected, "typo_corrected"),
            (swap, "swapped_2002_2003"),
            (unswapped, "unswapped_in_window"),
        ).alias(rule_column("agtron")),
    )
