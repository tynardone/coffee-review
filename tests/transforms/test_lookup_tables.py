"""Tests for coffee_review.transforms.lookup_tables: the hand-kept CSVs."""

from __future__ import annotations

import csv
from importlib.resources import files

from coffee_review.transforms.lookup_tables import load_places, normalise_place

KINDS = {"country", "state", "province", "island", "area"}


def place_rows() -> list[dict[str, str]]:
    text = (
        files("coffee_review.transforms.lookups")
        .joinpath("places.csv")
        .read_text(encoding="utf-8")
    )
    return list(csv.DictReader(text.splitlines()))


def test_place_aliases_unique() -> None:
    aliases = [normalise_place(row["alias"]) for row in place_rows()]

    duplicates = {a for a in aliases if aliases.count(a) > 1}
    assert duplicates == set()


def test_place_kinds_known() -> None:
    assert {row["kind"] for row in place_rows()} <= KINDS


def test_every_country_has_its_own_row() -> None:
    # A state, island or roaster country must name a country the table knows.
    places = load_places()
    countries = {p.country for p in places.values() if p.kind == "country"}

    for alias, place in places.items():
        if place.kind != "area":
            assert place.country in countries, alias
        assert place.roaster_country in countries | {"Multiple"}, alias


def test_normalise_place() -> None:
    assert normalise_place("  “Big Island”  of Hawai’i. ") == "big island of hawai'i"
    assert normalise_place("Kaʻū") == "ka'ū"
    assert normalise_place("MInnesota") == "minnesota"


def test_hawaii_roasters_are_in_the_united_states() -> None:
    hawaii = load_places()["hawaii"]

    assert (hawaii.country, hawaii.kind, hawaii.roaster_country) == (
        "Hawaii",
        "country",
        "United States",
    )
