"""Tests for coffee_review.transforms.places: origins and roaster locations."""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl
import pytest

from coffee_review.transforms.lookup_tables import load_places
from coffee_review.transforms.places import (
    OriginResult,
    RoasterResult,
    clean_origin,
    clean_roaster_location,
    place_origin,
    place_roaster,
)
from tests.transforms.helpers import run

if TYPE_CHECKING:
    from coffee_review.models.clean import CleanRule

PLACES = load_places()


def origin(text: str) -> OriginResult:
    return place_origin(text, PLACES)


def roaster(text: str) -> RoasterResult:
    return place_roaster(text, PLACES)


@pytest.mark.parametrize(
    ("text", "country", "region"),
    [
        ("Guji Zone, Oromia Region, southern Ethiopia", "Ethiopia", "Guji, Oromia"),
        ("Colombia", "Colombia", None),
        ("Antigua, Guatemala", "Guatemala", "Antigua"),
        (
            "Yirgacheffe growing region, south-central Ethiopia",
            "Ethiopia",
            "Yirgacheffe",
        ),
        ("Huila Department, Colombia", "Colombia", "Huila"),
    ],
)
def test_origin_country_and_region(text: str, country: str, region: str | None) -> None:
    assert origin(text) == OriginResult(country, region, ())


def test_origin_islands() -> None:
    # An island names its country, and is kept as region detail.
    assert origin("Sumatra") == OriginResult("Indonesia", "Sumatra", ())
    assert origin("Lintong growing region, northern Sumatra, Indonesia") == (
        OriginResult("Indonesia", "Lintong, Sumatra", ())
    )


@pytest.mark.parametrize(
    "text",
    ["Brazil, Central America, Colombia", "Latin America", "Kenya and Ethiopia"],
)
def test_origin_multiple(text: str) -> None:
    assert origin(text) == OriginResult("Multiple", None, ("multiple_origins",))


@pytest.mark.parametrize(
    "text", ["Not disclosed", "Not disclosed.", "Not Disclosed", " not disclosed "]
)
def test_not_disclosed_variants(text: str) -> None:
    assert origin(text) == OriginResult(None, None, ("not_disclosed",))


@pytest.mark.parametrize(
    "text",
    [
        "Big Island of Hawai’i",
        "“Big Island” of Hawai’i",
        '"Big Island" of Hawaii',
        "Kona, Hawai'i",
        "Kaʻū, Hawaii",
        "Kona growing region, Big Island of Hawai'i",
    ],
)
def test_hawaii_spellings_all_place(text: str) -> None:
    assert origin(text).country == "Hawaii"


def test_hawaii_region_detail() -> None:
    assert origin("Kona, Hawai'i") == OriginResult("Hawaii", "Kona", ())


def test_puerto_rico_own_country() -> None:
    assert origin("Yauco, Puerto Rico") == OriginResult("Puerto Rico", "Yauco", ())


def test_origin_unplaced() -> None:
    assert origin("Somewhere Unknown") == OriginResult(None, None, ("unplaced",))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Bentonville, Arkansas", ("Bentonville", "Arkansas", "United States")),
        ("Toronto, Ontario, Canada", ("Toronto", "Ontario", "Canada")),
        ("Chia-Yi, Taiwan", ("Chia-Yi", None, "Taiwan")),
        ("Taiwan", (None, None, "Taiwan")),
        ("Holualoa, Hawai’i Island, Hawai’i", ("Holualoa", "Hawaii", "United States")),
        ("Kailua-Kona, Hawaii", ("Kailua-Kona", "Hawaii", "United States")),
        ("Minneapolis, MInnesota", ("Minneapolis", "Minnesota", "United States")),
        ("Helena, Montana.", ("Helena", "Montana", "United States")),
    ],
)
def test_roaster_locations(
    text: str, expected: tuple[str | None, str | None, str | None]
) -> None:
    city, region, country = expected

    assert roaster(text) == RoasterResult(city, region, country, ())


def test_roaster_unplaced() -> None:
    assert roaster("Atlantis") == RoasterResult("Atlantis", None, None, ("unplaced",))


def test_clean_origin_joins_back() -> None:
    values = ["Antigua, Guatemala", "Antigua, Guatemala", "Not disclosed", None]

    result = run(lambda f: clean_origin(f, PLACES), "coffee_origin", values)

    assert result.height == 4
    assert result["origin_country"].to_list() == ["Guatemala", "Guatemala", None, None]
    assert result["origin_region"].to_list() == ["Antigua", "Antigua", None, None]
    assert result["_rule_coffee_origin"].to_list() == [[], [], ["not_disclosed"], []]
    assert result["slug"].to_list() == ["s0", "s1", "s2", "s3"]  # order kept


def test_clean_roaster_location_columns() -> None:
    result = run(
        lambda f: clean_roaster_location(f, PLACES),
        "roaster_location",
        ["Toronto, Ontario, Canada", "Atlantis"],
    )

    assert result.select(
        "roaster_city", "roaster_region", "roaster_country"
    ).rows() == [
        ("Toronto", "Ontario", "Canada"),
        ("Atlantis", None, None),
    ]
    assert result["_rule_roaster_location"].to_list() == [[], ["unplaced"]]
    assert result.schema["_rule_roaster_location"] == pl.List(pl.String)


@pytest.mark.parametrize(
    ("text", "country"),
    [("Columbia", "Colombia"), ("Nyeri growing region, Kenia", "Kenya")],
)
def test_origin_close_spelling(text: str, country: str) -> None:
    result = origin(text)

    assert (result.country, result.rules) == (country, ("close_spelling",))


@pytest.mark.parametrize(
    ("text", "region"),
    [
        ("Sacramento, Calfornia", "California"),
        ("Seattle, Washingto", "Washington"),
        ("Vancouver, British Colombia", "British Columbia"),
    ],
)
def test_roaster_close_spelling(text: str, region: str) -> None:
    result = roaster(text)

    assert result.region == region
    assert result.rules == ("close_spelling",)


def test_exact_match_beats_close_spelling() -> None:
    # Bolivar is close to Bolivia, but Colombia matched exactly: no guessing.
    assert origin("Bolivar, Colombia") == OriginResult("Colombia", "Bolivar", ())


def test_short_or_distant_spellings_stay_unplaced() -> None:
    assert origin("Pery").rules == ("unplaced",)  # too short to trust
    assert origin("Atlantis").rules == ("unplaced",)


@pytest.mark.parametrize(
    ("text", "country", "region"),
    [
        ("Huila Colombia", "Colombia", "Huila"),
        ("Urrao, Antioquia Colombia", "Colombia", "Urrao, Antioquia"),
        ("Harrar region of eastern Ethiopia", "Ethiopia", "Harrar"),
        ("Antioquia Department, central northwest Colombia.", "Colombia", "Antioquia"),
        ("Nyeri growing region, south-central Kenya", "Kenya", "Nyeri"),
    ],
)
def test_country_inside_a_part(text: str, country: str, region: str) -> None:
    assert origin(text) == OriginResult(country, region, ())


def test_roaster_without_comma() -> None:
    assert roaster("Scottsdale Arizona") == RoasterResult(
        "Scottsdale", "Arizona", "United States", ()
    )


def test_percentages_ignored() -> None:
    assert origin("Kenya 40%, Colombia 30%, Costa Rica 30%") == OriginResult(
        "Multiple", None, ("multiple_origins",)
    )


def test_no_close_spelling_when_a_country_matched() -> None:
    # Chinas is a town in Huila, close to "China": Colombia matched exactly.
    assert origin("Chinas, San Agustín, Huila, Colombia") == OriginResult(
        "Colombia", "Chinas, San Agustín, Huila", ()
    )


def test_country_first_in_a_part() -> None:
    assert origin("Kenya Gatugi AB") == OriginResult("Kenya", "Gatugi AB", ())


def test_democratic_republic_of_congo_without_the() -> None:
    assert origin("Lake Kivu, Democratic Republic of Congo") == OriginResult(
        "Democratic Republic of the Congo", "Lake Kivu", ()
    )


@pytest.mark.parametrize(
    ("text", "rule"),
    [
        ("Not disclosed", "not_disclosed"),
        ("Not Disclosed.", "not_disclosed"),
        ("NA", "unknown_marker"),
        ("n/a", "unknown_marker"),
    ],
)
def test_roaster_markers(text: str, rule: CleanRule) -> None:
    assert roaster(text) == RoasterResult(None, None, None, (rule,))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Chicago, IL", ("Chicago", "Illinois", "United States")),
        ("Soquel, CA", ("Soquel", "California", "United States")),
        ("Washington, DC", ("Washington", "District of Columbia", "United States")),
    ],
)
def test_roaster_state_codes(
    text: str, expected: tuple[str | None, str | None, str | None]
) -> None:
    city, region, country = expected

    assert roaster(text) == RoasterResult(city, region, country, ())


def test_state_codes_match_whole_parts_only() -> None:
    # "in" inside a part must not become Indiana.
    assert roaster("Roasted and packed in Colombia").region is None


def test_text_before_a_country_is_not_a_city() -> None:
    assert roaster("Roasted and packed in Colombia") == RoasterResult(
        None, None, "Colombia", ()
    )


def test_short_codes_not_matched_inside_a_part() -> None:
    assert origin("Grown in").rules == ("unplaced",)  # not Indiana


def test_origin_na_is_unknown() -> None:
    assert origin("NA") == OriginResult(None, None, ("unknown_marker",))
