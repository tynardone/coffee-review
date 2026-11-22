"""Tests for coffee_review.transforms.roast: roast level and Agtron readings."""

from __future__ import annotations

from datetime import date

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from coffee_review.models.clean import ROAST_LEVELS
from coffee_review.transforms.roast import (
    SWAP_FIRST_MONTH,
    SWAP_LAST_MONTH,
    clean_agtron,
    clean_roast_level,
)
from tests.transforms.helpers import run

OUTSIDE = date(2010, 1, 1)
IN_WINDOW = date(2002, 12, 1)

type Agtron = tuple[int | None, int | None, bool, bool, list[str]]


def agtron(text: str | None, month: date = OUTSIDE) -> Agtron:
    """(whole, ground, swapped, suspect, rules) for one reading."""
    result = run(clean_agtron, "agtron", [text], review_month=[month])
    row = result.row(0, named=True)
    return (
        row["agtron_whole"],
        row["agtron_ground"],
        row["agtron_swapped"],
        row["agtron_suspect"],
        row["_rule_agtron"],
    )


def test_roast_levels() -> None:
    values = [*ROAST_LEVELS, "medium-dark", "VERY DARK", "NA"]

    result = run(clean_roast_level, "roast_level", values)

    assert result["roast_level"].to_list() == [
        *ROAST_LEVELS,
        "Medium-Dark",
        "Very Dark",
        None,
    ]
    assert result["_rule_roast_level"].to_list()[-1] == ["unknown_marker"]


def test_roast_level_unparsed() -> None:
    result = run(clean_roast_level, "roast_level", ["Cinnamon", None])

    assert result["roast_level"].to_list() == [None, None]
    assert result["_rule_roast_level"].to_list() == [["unparsed"], []]


def test_agtron_plain() -> None:
    assert agtron("48/65") == (48, 65, False, False, [])


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/", (None, None, False, False, ["unknown_marker"])),
        ("NA/NA", (None, None, False, False, ["unknown_marker"])),
        ("NA/", (None, None, False, False, ["unknown_marker"])),
        ("0/0", (None, None, False, False, ["not_measured"])),
        ("0/57", (None, 57, False, False, ["not_measured"])),
        ("/64", (None, 64, False, False, ["unknown_marker"])),
        ("38/na", (38, None, False, False, ["unknown_marker"])),
    ],
)
def test_agtron_unknowns(text: str, expected: Agtron) -> None:
    assert agtron(text) == expected


def test_agtron_template_label() -> None:
    assert agtron("g/wb") == (None, None, False, False, ["template_label"])


def test_agtron_unparsed() -> None:
    assert agtron("58") == (None, None, False, False, ["unparsed"])
    assert agtron("abc/60") == (None, 60, False, False, ["unparsed"])
    assert agtron(None) == (None, None, False, False, [])


def test_agtron_swap_in_window() -> None:
    assert agtron("48/40", IN_WINDOW) == (40, 48, True, False, ["swapped_2002_2003"])


def test_agtron_unswapped_in_window_is_suspect() -> None:
    assert agtron("45/60", date(2002, 7, 1)) == (
        45,
        60,
        False,
        True,
        ["unswapped_in_window"],
    )


@pytest.mark.parametrize(
    ("month", "swapped"),
    [
        (date(2002, 5, 1), False),
        (date(2002, 6, 1), True),
        (date(2003, 3, 1), True),
        (date(2003, 4, 1), False),
    ],
)
def test_swap_window_edges(month: date, swapped: bool) -> None:
    whole, ground, was_swapped, suspect, _ = agtron("48/40", month)

    assert was_swapped is swapped
    assert (whole, ground) == ((40, 48) if swapped else (48, 40))
    assert suspect is False


def corrected(text: str, month: date = OUTSIDE) -> bool:
    result = run(clean_agtron, "agtron", [text], review_month=[month])
    flag: bool = result["agtron_corrected"].item()
    return flag


@pytest.mark.parametrize(
    ("text", "whole", "ground"),
    [
        pytest.param("6489/89", 64, 89, id="ground-appended"),
        pytest.param("4254/54", 42, 54, id="ground-appended-2"),
        pytest.param("547/74", 54, 74, id="ground-first-digit"),
        pytest.param("578/80", 57, 80, id="ground-first-digit-2"),
        pytest.param("5252/70", 52, 70, id="doubled"),
        pytest.param("555/73", 55, 73, id="last-digit-doubled"),
        pytest.param("54/7474", 54, 74, id="ground-side-doubled"),
    ],
)
def test_typo_patterns_corrected(text: str, whole: int, ground: int) -> None:
    assert agtron(text) == (whole, ground, False, False, ["typo_corrected"])
    assert corrected(text) is True


def test_unexplained_typo_stays_suspect() -> None:
    assert agtron("48/689") == (48, 689, False, True, ["suspect_value"])
    assert corrected("48/689") is False


def test_correction_must_look_normal() -> None:
    # 857/74 → 85 would be darker on the ground side than whole bean: no fix.
    assert agtron("857/74") == (857, 74, False, True, ["suspect_value"])


def test_corrected_in_window_follows_printed_order() -> None:
    # In the window the site printed ground first: 7474/54 → 74/54, then swapped.
    assert agtron("7474/54", IN_WINDOW) == (
        54,
        74,
        True,
        False,
        ["typo_corrected", "swapped_2002_2003"],
    )


def test_decimal_and_light_readings() -> None:
    assert agtron("43.5/60") == (44, 60, False, True, ["suspect_value"])
    assert agtron("82/104") == (82, 104, False, False, [])
    assert corrected("82/104") is False


@settings(max_examples=25)
@given(
    st.lists(
        st.tuples(
            st.integers(min_value=151, max_value=9999),
            st.integers(min_value=10, max_value=99),
        ),
        min_size=1,
        max_size=50,
    )
)
def test_corrections_are_two_digit_prefixes(rows: list[tuple[int, int]]) -> None:
    texts = [f"{whole}/{ground}" for whole, ground in rows]
    result = run(clean_agtron, "agtron", texts, review_month=[OUTSIDE] * len(rows))
    outputs = zip(result["agtron_whole"], result["agtron_corrected"])

    for (whole, _), (out_whole, was_corrected) in zip(rows, outputs, strict=True):
        if was_corrected:
            assert 10 <= out_whole <= 99
            assert str(whole).startswith(str(out_whole))


def test_agtron_beyond_contract_is_dropped() -> None:
    assert agtron("72000/80") == (None, 80, False, True, ["suspect_value"])


def test_unparsed_month_is_outside_window() -> None:
    result = run(clean_agtron, "agtron", ["48/40"], review_month=[None])

    assert result["agtron_swapped"].to_list() == [False]


readings = st.integers(min_value=1, max_value=150)
months = st.dates(min_value=date(1997, 1, 1), max_value=date(2026, 12, 1)).map(
    lambda d: d.replace(day=1)
)


# Each example is a batch of up to 50 readings in one frame: planning a frame
# costs ~40ms, so 25 batches check ~1,250 readings in about a second.
@settings(max_examples=25)
@given(st.lists(st.tuples(readings, readings, months), min_size=1, max_size=50))
def test_swap_properties(rows: list[tuple[int, int, date]]) -> None:
    result = run(
        clean_agtron,
        "agtron",
        [f"{whole}/{ground}" for whole, ground, _ in rows],
        review_month=[month for *_, month in rows],
    )
    outputs = zip(
        result["agtron_whole"], result["agtron_ground"], result["agtron_swapped"]
    )

    for (whole, ground, month), (out_whole, out_ground, swapped) in zip(
        rows, outputs, strict=True
    ):
        if not SWAP_FIRST_MONTH <= month <= SWAP_LAST_MONTH:
            assert (out_whole, out_ground, swapped) == (whole, ground, False)
        else:
            assert out_ground >= out_whole
        if swapped:
            assert (out_whole, out_ground) == (ground, whole)


def test_reading_that_rounds_to_zero_is_suspect_not_a_crash() -> None:
    # 0.4 rounds to 0, which the contract refuses (gt=0).
    assert agtron("55/0.4") == (55, None, False, True, ["suspect_value"])
