"""Tests for coffee_review.transforms.growing_regions: an origin's growing region."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from coffee_review.transforms.growing_regions import (
    RegionResult,
    build_growing_regions,
    fold_region,
    name_growing_region,
)
from coffee_review.transforms.lookup_tables import load_growing_regions

REGIONS = build_growing_regions(
    [
        ("Ethiopia", "Yirgacheffe", "Yirgacheffe"),
        ("Ethiopia", "Gedeb", "Yirgacheffe"),
        ("Ethiopia", "Kochere", "Yirgacheffe"),
        ("Ethiopia", "Sidama", "Sidama"),
        ("Ethiopia", "Guji", "Guji"),
        ("Ethiopia", "Oromia", None),
        ("Colombia", "Cauca", "Cauca"),
        ("Colombia", "Valle del Cauca", "Valle del Cauca"),
        ("Colombia", "Nariño", "Nariño"),
        ("Hawaii", "Kona", "Kona"),
        ("Hawaii", "Kaʻū", "Kaʻū"),
        ("Brazil", "Minas Gerais", "Minas Gerais"),
        ("Brazil", "Sul de Minas", "Minas Gerais"),
    ]
)

COVERED = {
    "Ethiopia",
    "Colombia",
    "Kenya",
    "Guatemala",
    "Indonesia",
    "Costa Rica",
    "Panama",
    "Hawaii",
    "El Salvador",
    "Rwanda",
    "Brazil",
    "Peru",
    "Nicaragua",
    "Honduras",
    "Mexico",
}


def region(country: str | None, text: str | None) -> RegionResult:
    return name_growing_region(country, text, REGIONS)


# --- Naming the region ---------------------------------------------------------


def test_region_as_written_logs_nothing() -> None:
    assert region("Ethiopia", "Yirgacheffe") == RegionResult("Yirgacheffe", ())


def test_district_names_its_region() -> None:
    assert region("Ethiopia", "Gedeb, Gedeo") == RegionResult(
        "Yirgacheffe", ("growing_region",)
    )


def test_two_spellings_of_one_region_is_one() -> None:
    assert region("Ethiopia", "Kochere, Yirgacheffe").region == "Yirgacheffe"
    assert region("Brazil", "Sul de Minas, southern Minas Gerais").region == (
        "Minas Gerais"
    )


def test_two_regions_are_multiple() -> None:
    assert region("Ethiopia", "Yirgacheffe, Sidama") == RegionResult(
        "Multiple", ("multiple_regions",)
    )


def test_longer_alias_wins() -> None:
    assert region("Colombia", "Trujillo, Valle del Cauca") == RegionResult(
        "Valle del Cauca", ("growing_region",)
    )


def test_whole_words_only() -> None:
    assert region("Hawaii", "Konawe") == RegionResult(None, ("unknown_region",))


def test_broad_name_alone() -> None:
    assert region("Ethiopia", "Oromia") == RegionResult(None, ("broad_region",))


def test_broad_name_beside_a_region() -> None:
    assert region("Ethiopia", "Guji, Oromia") == RegionResult(
        "Guji", ("growing_region",)
    )


def test_unknown_in_covered_country() -> None:
    assert region("Ethiopia", "Somewhere") == RegionResult(None, ("unknown_region",))


@pytest.mark.parametrize(
    ("country", "text"),
    [
        ("Burundi", "Kayanza"),
        ("Multiple", "Yirgacheffe"),
        (None, "Yirgacheffe"),
        ("Ethiopia", None),
    ],
    ids=["uncovered-country", "blend", "no-country", "no-region-text"],
)
def test_uncovered_country_and_blends(country: str | None, text: str | None) -> None:
    assert region(country, text) == RegionResult(None, ())


def test_accents_case_and_okina_ignored() -> None:
    assert region("Colombia", "NARINO").region == "Nariño"
    assert region("Hawaii", "Ka'u").region == "Kaʻū"
    assert region("Hawaii", "Kau").region == "Kaʻū"


@given(st.text())
def test_fold_is_idempotent(text: str) -> None:
    assert fold_region(fold_region(text)) == fold_region(text)


# --- The table -------------------------------------------------------------------


def test_alias_with_two_regions_rejected() -> None:
    with pytest.raises(ValueError, match="guji"):
        build_growing_regions(
            [("Ethiopia", "Guji", "Guji"), ("Ethiopia", "guji", "Sidama")]
        )


def test_table_covers_the_fifteen_countries() -> None:
    assert set(load_growing_regions()) == COVERED


def test_every_region_is_its_own_alias() -> None:
    for country, aliases in load_growing_regions().items():
        names = {name for _, name in aliases if name is not None}
        for name in names:
            assert (fold_region(name), name) in aliases, (country, name)
