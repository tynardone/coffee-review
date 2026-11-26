"""Tests for coffee_review.pipeline.roaster_review: the sheet and applying it."""

from __future__ import annotations

from datetime import date

import polars as pl

from coffee_review.models.clean import REVIEW_SHEET_COLUMNS, REVIEW_SHEET_SCHEMA
from coffee_review.pipeline.roaster_review import build_review_sheet
from coffee_review.transforms.roasters import NameDecision, NameMap

HOLUALOA = ("Holualoa", "United States")


def reviews(*rows: tuple[str, str, date, tuple[str, str] | None]) -> pl.DataFrame:
    """Rows as (spelling as written, clean roaster, month, (city, country))."""
    return pl.DataFrame(
        {
            "raw_roaster": [r[0] for r in rows],
            "roaster": [r[1] for r in rows],
            "review_month": [r[2] for r in rows],
            "roaster_city": [r[3][0] if r[3] else None for r in rows],
            "roaster_country": [r[3][1] if r[3] else None for r in rows],
        },
        schema={
            "raw_roaster": pl.String,
            "roaster": pl.String,
            "review_month": pl.Date,
            "roaster_city": pl.String,
            "roaster_country": pl.String,
        },
    )


NO_GREEN = pl.DataFrame(
    schema={"raw_roaster": pl.String, "roaster": pl.String, "review_month": pl.Date}
)
NO_SHEET = pl.DataFrame(schema=REVIEW_SHEET_SCHEMA)

HULA = reviews(
    ("Hula Daddy", "Hula Daddy", date(2005, 10, 1), HOLUALOA),
    ("Hula Daddy", "Hula Daddy", date(2016, 11, 1), HOLUALOA),
    ("Hula Daddy Kona Coffee", "Hula Daddy Kona Coffee", date(2020, 1, 1), HOLUALOA),
)
HULA_NAMES = NameMap(
    {
        "Hula Daddy": NameDecision(
            "Hula Daddy", ("possible_match",), ("Hula Daddy Kona Coffee",)
        ),
        "Hula Daddy Kona Coffee": NameDecision("Hula Daddy Kona Coffee", ()),
    },
    (),
)


def test_possible_match_row_has_both_sides() -> None:
    sheet = build_review_sheet(HULA, NO_GREEN, HULA_NAMES, NO_SHEET)

    assert sheet.columns == list(REVIEW_SHEET_COLUMNS)
    assert sheet.row(0, named=True) == {
        "kind": "possible_match",
        "spelling": "Hula Daddy",
        "reviews": "2",
        "places": "Holualoa, United States",
        "first": "2005-10",
        "last": "2016-11",
        "candidate": "Hula Daddy Kona Coffee",
        "candidate_reviews": "1",
        "candidate_places": "Holualoa, United States",
        "candidate_first": "2020-01",
        "candidate_last": "2020-01",
        "decision": "",
        "name": "",
    }


def test_one_row_per_candidate() -> None:
    frame = reviews(
        ("abcde", "abcde", date(2020, 1, 1), None),
        ("abcdx", "abcdx", date(2020, 1, 1), None),
        ("abcdy", "abcdy", date(2020, 1, 1), None),
    )
    names = NameMap(
        {"abcde": NameDecision("abcde", ("possible_match",), ("abcdx", "abcdy"))}, ()
    )

    sheet = build_review_sheet(frame, NO_GREEN, names, NO_SHEET)

    assert sheet["candidate"].to_list() == ["abcdx", "abcdy"]


def test_merged_row_names_what_it_merged_into() -> None:
    la = ("Los Angeles", "United States")
    frame = reviews(
        ("La Mill", "La Mill", date(2010, 1, 1), la),
        ("Lamill", "La Mill", date(2011, 1, 1), la),
    )
    names = NameMap({"Lamill": NameDecision("La Mill", ("close_spelling",))}, ())

    sheet = build_review_sheet(frame, NO_GREEN, names, NO_SHEET)

    row = sheet.row(0, named=True)
    assert (row["kind"], row["spelling"], row["candidate"]) == (
        "merged",
        "Lamill",
        "La Mill",
    )
    assert (row["reviews"], row["candidate_reviews"]) == ("1", "2")


def test_green_reviews_count_without_places() -> None:
    green = pl.DataFrame(
        {
            "raw_roaster": ["Hula Daddy"],
            "roaster": ["Hula Daddy"],
            "review_month": [date(2001, 1, 1)],
        }
    )

    sheet = build_review_sheet(HULA, green, HULA_NAMES, NO_SHEET)

    row = sheet.row(0, named=True)
    assert (row["reviews"], row["first"]) == ("3", "2001-01")
    assert row["places"] == "Holualoa, United States"


def test_places_without_a_city_show_the_country() -> None:
    frame = reviews(("San Rafael", "San Rafael", date(1997, 7, 1), ("", "Guatemala")))
    frame = frame.with_columns(pl.lit(None, pl.String).alias("roaster_city"))
    names = NameMap(
        {"San Rafael": NameDecision("San Rafael", ("possible_match",), ("X",))}, ()
    )

    sheet = build_review_sheet(frame, NO_GREEN, names, NO_SHEET)

    assert sheet["places"].to_list() == ["Guatemala"]


def previous(**fields: str) -> pl.DataFrame:
    row = dict.fromkeys(REVIEW_SHEET_COLUMNS, "") | fields
    return pl.DataFrame([row], schema=REVIEW_SHEET_SCHEMA)


def test_decisions_carried_over() -> None:
    old = previous(
        kind="possible_match",
        spelling="Hula Daddy",
        candidate="Hula Daddy Kona Coffee",
        decision="same",
        name="Hula Daddy Kona",
    )

    sheet = build_review_sheet(HULA, NO_GREEN, HULA_NAMES, old)

    assert sheet.select("decision", "name").row(0) == ("same", "Hula Daddy Kona")


def test_decisions_for_closed_pairs_dropped() -> None:
    old = previous(
        kind="possible_match", spelling="Gone", candidate="X", decision="same"
    )

    sheet = build_review_sheet(HULA, NO_GREEN, HULA_NAMES, old)

    assert sheet["spelling"].to_list() == ["Hula Daddy"]


def test_sheet_sorted_by_kind_then_spelling() -> None:
    frame = reviews(
        ("B", "B", date(2020, 1, 1), None),
        ("A", "A", date(2020, 1, 1), None),
        ("Z", "Y", date(2020, 1, 1), None),
        ("Y", "Y", date(2020, 1, 1), None),
    )
    names = NameMap(
        {
            "B": NameDecision("B", ("possible_match",), ("X",)),
            "A": NameDecision("A", ("possible_match",), ("X",)),
            "Z": NameDecision("Y", ("close_spelling",)),
        },
        (),
    )

    sheet = build_review_sheet(frame, NO_GREEN, names, NO_SHEET)

    assert sheet.select("kind", "spelling").rows() == [
        ("merged", "Z"),
        ("possible_match", "A"),
        ("possible_match", "B"),
    ]


def test_nothing_to_review_is_an_empty_sheet() -> None:
    names = NameMap({"Hula Daddy": NameDecision("Hula Daddy", ())}, ())

    sheet = build_review_sheet(HULA, NO_GREEN, names, NO_SHEET)

    assert sheet.height == 0
    assert sheet.schema == pl.Schema(REVIEW_SHEET_SCHEMA)
