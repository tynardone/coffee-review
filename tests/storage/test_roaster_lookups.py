"""Tests for coffee_review.storage.roaster_lookups: writing the hand-kept CSVs."""

from __future__ import annotations

from typing import TYPE_CHECKING

from coffee_review.storage.roaster_lookups import RoasterLookups, open_roaster_lookups
from coffee_review.transforms.lookup_tables import (
    load_distinct_roasters,
    load_listed_roasters,
)

if TYPE_CHECKING:
    from pathlib import Path


def lookups(tmp_path: Path, listed: str = "", distinct: str = "") -> RoasterLookups:
    (tmp_path / "roasters.csv").write_text("spelling,roaster\n" + listed, "utf-8")
    (tmp_path / "distinct_roasters.csv").write_text(
        "spelling,other\n" + distinct, "utf-8"
    )
    return RoasterLookups(tmp_path)


def test_lookups_add_new_rows_only(tmp_path: Path) -> None:
    store = lookups(tmp_path, listed="A,B\n")

    added = store.add([("A", "B"), ("C", "D")], [("E", "F")])

    assert added == ([("C", "D")], [("E", "F")])
    assert store.listed_rows() == [("A", "B"), ("C", "D")]
    assert store.distinct_rows() == [("E", "F")]


def test_lookups_quote_commas(tmp_path: Path) -> None:
    store = lookups(tmp_path)
    row = ("Shanghai Carmo Trading Co., LTD", "Shanghai Creation Trading Co, Ltd.")

    store.add([], [row])

    assert RoasterLookups(tmp_path).distinct_rows() == [row]


def test_lookups_keep_header(tmp_path: Path) -> None:
    store = lookups(tmp_path)

    store.add([("A", "B")], [("C", "D")])

    assert (tmp_path / "roasters.csv").read_text("utf-8") == "spelling,roaster\nA,B\n"
    assert (
        (tmp_path / "distinct_roasters.csv")
        .read_text("utf-8")
        .startswith("spelling,other\n")
    )


def test_adding_nothing_leaves_files_alone(tmp_path: Path) -> None:
    store = lookups(tmp_path, listed="A,B\n")
    before = (tmp_path / "roasters.csv").stat().st_mtime_ns

    assert store.add([("A", "B")], []) == ([], [])
    assert (tmp_path / "roasters.csv").stat().st_mtime_ns == before


def test_package_lookups_are_found() -> None:
    store = open_roaster_lookups()

    assert store.listed_rows() == list(load_listed_roasters().items())
    assert store.distinct_rows() == list(load_distinct_roasters())
