"""Tests for coffee_review.transforms.scores."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from coffee_review.transforms.scores import clean_rating, clean_scores
from tests.transforms.helpers import run


def scores(values: list[str | None]) -> tuple[list[float | None], list[list[str]]]:
    result = run(lambda f: clean_scores(f, ["aroma"]), "aroma", values)
    return result["aroma"].to_list(), result["_rule_aroma"].to_list()


def test_rating_nr_and_numbers() -> None:
    result = run(clean_rating, "rating", ["82", "NR", None])

    assert result["rating"].to_list() == [82, None, None]
    assert result["_rule_rating"].to_list() == [[], ["not_rated"], []]


def test_rating_out_of_range_is_suspect() -> None:
    result = run(clean_rating, "rating", ["820", "abc"])

    assert result["rating"].to_list() == [None, None]
    assert result["_rule_rating"].to_list() == [["suspect_value"], ["unparsed"]]


def test_scores_whole_numbers() -> None:
    assert scores(["8", "10"]) == ([8.0, 10.0], [[], []])


def test_scores_decimals_kept() -> None:
    values, _ = scores(["6.3"])

    assert values[0] == pytest.approx(6.3)


@pytest.mark.parametrize("marker", [" NA ", "na", "Na", "n/a", "N/A", "NR"])
def test_unknown_markers_any_case(marker: str) -> None:
    assert scores([marker]) == ([None], [["unknown_marker"]])


@pytest.mark.parametrize("word", ["Low", "Very Low", "Moderate"])
def test_word_scores(word: str) -> None:
    assert scores([word]) == ([None], [["word_score"]])


def test_labelled_with_milk() -> None:
    result = run(
        lambda f: clean_scores(f, ["with_milk"]), "with_milk", ["Flavor in milk: 5"]
    )

    assert result["with_milk"].to_list() == [5.0]
    assert result["_rule_with_milk"].to_list() == [["labelled_score"]]


def test_scores_out_of_range_is_suspect() -> None:
    assert scores(["0", "11"]) == ([None, None], [["suspect_value"], ["suspect_value"]])


def test_scores_unparsed() -> None:
    assert scores(["8+", None]) == ([None, None], [["unparsed"], []])


def test_several_fields_at_once() -> None:
    result = run(
        lambda f: clean_scores(f, ["aroma", "body"]), "aroma", ["8"], raw_body=["NA"]
    )

    assert result["aroma"].to_list() == [8.0]
    assert result["body"].to_list() == [None]
    assert result["_rule_body"].to_list() == [["unknown_marker"]]


@given(st.decimals(min_value=0, max_value=12, places=1, allow_nan=False))
def test_scores_in_range(value: object) -> None:
    text = str(value)
    [result], [rules] = scores([text])

    if 1 <= float(text) <= 10:
        assert result == pytest.approx(float(text), abs=1e-5)
        assert rules == []
    else:
        assert result is None
        assert rules == ["suspect_value"]
