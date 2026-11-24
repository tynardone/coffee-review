"""Tests for coffee_review.transforms.lookup_tables: the hand-kept CSVs."""

from __future__ import annotations

import csv
import re
from collections.abc import Mapping
from importlib.resources import files

import pytest

from coffee_review.transforms.lookup_tables import (
    load_currencies,
    load_distinct_roasters,
    load_listed_roasters,
    load_places,
    load_units,
    normalise_place,
    normalise_symbol,
    normalise_unit,
)

KINDS = {"country", "state", "province", "island", "region", "area"}


def rows(name: str) -> list[dict[str, str]]:
    text = (
        files("coffee_review.transforms.lookups")
        .joinpath(name)
        .read_text(encoding="utf-8")
    )
    return list(csv.DictReader(text.splitlines()))


def place_rows() -> list[dict[str, str]]:
    return rows("places.csv")


def test_place_aliases_unique() -> None:
    # Across both tables: a spelling means one place.
    aliases = [
        normalise_place(row["alias"])
        for row in rows("countries.csv") + rows("places.csv")
    ]

    duplicates = {a for a in aliases if aliases.count(a) > 1}
    assert duplicates == set()


@pytest.mark.parametrize(
    ("spelling", "country"),
    [
        ("Bhutan", "Bhutan"),  # never in a review so far
        ("Viet Nam", "Vietnam"),
        ("Lao People's Democratic Republic", "Laos"),
        ("Côte d'Ivoire", "Côte d'Ivoire"),
        ("Ivory Coast", "Côte d'Ivoire"),
        ("Brasil", "Brazil"),  # hand-kept spelling
    ],
)
def test_every_country_is_known(spelling: str, country: str) -> None:
    place = load_places()[normalise_place(spelling)]

    assert (place.country, place.kind) == (country, "country")


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


def test_currency_and_unit_aliases_unique() -> None:
    for name, normalise in (
        ("currencies.csv", normalise_symbol),
        ("units.csv", normalise_unit),
    ):
        aliases = [normalise(row["alias"]) for row in rows(name)]
        assert len(aliases) == len(set(aliases)), name


def test_currency_codes_are_iso_shaped() -> None:
    codes = set(load_currencies().values())
    codes |= {row["currency"] for row in rows("roaster_currencies.csv")}

    assert all(re.fullmatch(r"[A-Z]{3}", code) for code in codes)


def test_every_iso_currency_code_is_known() -> None:
    currencies = load_currencies()

    assert currencies["GTQ"] == "GTQ"
    assert currencies["LAK"] == "LAK"


def test_units_have_a_kind_and_weights_have_grams() -> None:
    for unit in load_units().values():
        assert unit.kind in {"weight", "volume", "count"}
        assert (unit.grams is not None) == (unit.kind == "weight")


def test_roaster_currencies_name_known_countries() -> None:
    countries = {p.country for p in load_places().values()}

    for row in rows("roaster_currencies.csv"):
        assert row["roaster_country"] in countries | {"*"}, row


def test_areas_name_known_areas_and_countries() -> None:
    places = load_places()
    areas = {p.name for p in places.values() if p.kind == "area"}
    countries = {p.country for p in places.values() if p.kind == "country"}

    for row in rows("areas.csv"):
        assert row["area"] in areas, row
        assert row["country"] in countries, row


def test_roaster_lookups_load() -> None:
    assert isinstance(load_listed_roasters(), Mapping)
    assert all(isinstance(pair, tuple) for pair in load_distinct_roasters())


def test_distinct_roaster_rows_name_two_spellings() -> None:
    assert all(a != b for a, b in load_distinct_roasters())


def test_listed_roaster_spellings_unique() -> None:
    spellings = [row["spelling"] for row in rows("roasters.csv")]
    assert len(spellings) == len(set(spellings))
