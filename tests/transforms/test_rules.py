"""Tests for coffee_review.transforms.rules: rule columns and the cleaning log."""

from __future__ import annotations

import polars as pl

from coffee_review.models.clean import GREEN_SCHEMA, LOG_SCHEMA, ROASTED_SCHEMA
from coffee_review.transforms.rules import (
    CLEANED_FIELDS,
    extract_log,
    raw,
    rule_column,
    rules_when,
    unlogged_nulls,
)

RULES = pl.List(pl.String)


def test_column_names() -> None:
    assert raw("agtron") == "raw_agtron"
    assert rule_column("agtron") == "_rule_agtron"


def test_rules_when_collects_true_conditions() -> None:
    frame = pl.DataFrame({"x": [5, 50]})

    result = frame.select(
        rules_when(
            (pl.col("x") > 1, "suspect_value"),
            (pl.col("x") > 10, "unparsed"),
            (pl.col("x") > 2, "not_measured"),
        ).alias("rules")
    )

    assert result["rules"].to_list() == [
        ["suspect_value", "not_measured"],
        ["suspect_value", "unparsed", "not_measured"],
    ]


def test_rules_when_none_true_is_empty_list() -> None:
    frame = pl.DataFrame({"x": [0]})

    result = frame.select(rules_when((pl.col("x") > 1, "unparsed")).alias("rules"))

    assert result["rules"].to_list() == [[]]


def test_extract_log_explodes_rules() -> None:
    frame = pl.DataFrame(
        {
            "slug": ["b", "a"],
            "raw_agtron": ["0/6489", "48/65"],
            "_rule_agtron": [["not_measured", "suspect_value"], []],
        },
        schema_overrides={"_rule_agtron": RULES},
    )

    log = extract_log(frame, "roasted")

    assert log.schema == LOG_SCHEMA
    assert log.rows() == [
        ("b", "roasted", "agtron", "not_measured", "0/6489"),
        ("b", "roasted", "agtron", "suspect_value", "0/6489"),
    ]


def test_extract_log_sorted_across_fields() -> None:
    frame = pl.DataFrame(
        {
            "slug": ["b", "a"],
            "raw_rating": ["NR", "NR"],
            "_rule_rating": [["not_rated"], ["not_rated"]],
            "raw_agtron": ["/", None],
            "_rule_agtron": [["unknown_marker"], []],
        },
        schema_overrides={"_rule_rating": RULES, "_rule_agtron": RULES},
    )

    log = extract_log(frame, "roasted")

    assert [(r[0], r[2]) for r in log.rows()] == [
        ("a", "rating"),
        ("b", "agtron"),
        ("b", "rating"),
    ]


def test_extract_log_empty() -> None:
    frame = pl.DataFrame(
        {"slug": ["a"], "raw_rating": ["82"], "_rule_rating": [[]]},
        schema_overrides={"_rule_rating": RULES},
    )

    log = extract_log(frame, "green")

    assert log.height == 0
    assert log.schema == LOG_SCHEMA


def rating_frame(raw_value: str | None, rules: list[str]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "slug": ["a"],
            "raw_rating": [raw_value],
            "rating": pl.Series([None], dtype=pl.Int16),
            "_rule_rating": [rules],
        },
        schema_overrides={"raw_rating": pl.String, "_rule_rating": RULES},
    )


def test_unlogged_nulls_found() -> None:
    found = unlogged_nulls(rating_frame("NR", []))

    assert found.rows() == [("a", "rating")]


def test_logged_null_is_not_reported() -> None:
    assert unlogged_nulls(rating_frame("NR", ["not_rated"])).height == 0


def test_null_input_is_not_reported() -> None:
    assert unlogged_nulls(rating_frame(None, [])).height == 0


def test_partly_filled_outputs_are_not_reported() -> None:
    frame = pl.DataFrame(
        {
            "slug": ["a"],
            "raw_agtron": ["x/57"],
            "agtron_whole": pl.Series([None], dtype=pl.Int16),
            "agtron_ground": pl.Series([57], dtype=pl.Int16),
            "_rule_agtron": [[]],
        },
        schema_overrides={"_rule_agtron": RULES},
    )

    assert unlogged_nulls(frame).height == 0


def test_cleaned_fields_name_real_clean_columns() -> None:
    # A typo here would hide a lost value from unlogged_nulls.
    clean_columns = set(ROASTED_SCHEMA) | set(GREEN_SCHEMA)

    for field, outputs in CLEANED_FIELDS.items():
        assert set(outputs) <= clean_columns, field
