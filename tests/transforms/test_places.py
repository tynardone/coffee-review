"""Tests for coffee_review.transforms.places: origins and roaster locations."""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl
import pytest

from coffee_review.transforms.growing_regions import build_growing_regions
from coffee_review.transforms.lookup_tables import load_growing_regions, load_places
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


# --- Origins ----------------------------------------------------------------------


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
        OriginResult("Indonesia", "Lintong, northern Sumatra", ())
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
    ("text", "country"),
    [("Columbia", "Colombia"), ("Thika growing region, Kenia", "Kenya")],
)
def test_origin_close_spelling(text: str, country: str) -> None:
    result = origin(text)

    assert (result.country, result.rules) == (country, ("close_spelling",))


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


def test_short_codes_not_matched_inside_a_part() -> None:
    assert origin("Grown in").rules == ("unplaced",)  # not Indiana


def test_origin_na_is_unknown() -> None:
    assert origin("NA") == OriginResult(None, None, ("unknown_marker",))


@pytest.mark.parametrize(
    ("text", "country", "region"),
    [
        ("Oromia Region; Guji Zone", "Ethiopia", "Oromia, Guji"),
        (
            "Worka-Sakaro, Gedeb District, Gedeo Zone",
            "Ethiopia",
            "Worka-Sakaro, Gedeb, Gedeo",
        ),
        ("Muranga County, Kenya", "Kenya", "Muranga"),
        ("Sandy Bay Valley, St. Helena", "Saint Helena", "Sandy Bay Valley"),
    ],
)
def test_coffee_regions_name_their_country(
    text: str, country: str, region: str
) -> None:
    assert origin(text) == OriginResult(country, region, ())


def test_regions_from_two_countries_are_multiple() -> None:
    assert origin("Yirgacheffe, Sidamo, Huehuetenango").country == "Multiple"


def test_slash_separates_origin_parts() -> None:
    assert origin("Africa/Arabia, Indo-Pacific").country == "Multiple"


def test_misspelt_not_disclosed() -> None:
    assert origin("Not discolosed") == OriginResult(None, None, ("not_disclosed",))


@pytest.mark.parametrize(
    ("text", "country", "region"),
    [
        ("Huehuetenango growing region, Guatemala.e", "Guatemala", "Huehuetenango"),
        ("Nariño Department, southwestern ColombiaNar", "Colombia", "Nariño"),
        ("Muranga County, south-central KenyaMur", "Kenya", "Muranga"),
    ],
)
def test_garbled_copy_of_the_country_is_not_region(
    text: str, country: str, region: str
) -> None:
    assert origin(text) == OriginResult(country, region, ("close_spelling",))


@pytest.mark.parametrize(
    ("text", "region"),
    [
        (
            "Carmo de Minas growing region, northeastern Minas Gerais State, Brazil",
            "Carmo de Minas, northeastern Minas Gerais",
        ),
        (
            "Southern Minas Gerais State and northern Sao Paulo State, Brazil",
            "Southern Minas Gerais, northern Sao Paulo",
        ),
    ],
)
def test_regions_with_direction_and_filler(text: str, region: str) -> None:
    # Real texts. A region part must never be mistaken for a garbled "Brazil".
    assert origin(text) == OriginResult("Brazil", region, ())


def test_near_spelling_of_a_region_is_not_a_garbled_country() -> None:
    # "Mogianna" is close to the region "Mogiana", in Brazil: it stays region
    # text, and isn't taken for a garbled "Brazil" and dropped.
    assert origin("Mogianna, Brazil") == OriginResult("Brazil", "Mogianna", ())


@pytest.mark.parametrize(
    ("text", "region"),
    [
        ("Lintong, North Sumatra, Indonesia", "Lintong, North Sumatra"),
        ("West Guji Zone, Oromia, Ethiopia", "West Guji, Oromia"),
        (
            "Holualoa, North Kona, Big Island of Hawai'i",
            "Holualoa, North Kona, Big Island",
        ),
    ],
)
def test_region_keeps_its_direction_words(text: str, region: str) -> None:
    # "North Sumatra" is a province and "West Guji" a zone: the direction is
    # part of the name.
    assert origin(text).region == region


@pytest.mark.parametrize(
    ("text", "country", "region"),
    [
        (
            "San Marcos growing region, southwestern Guatemala, Central America",
            "Guatemala",
            "San Marcos",
        ),
        ("Rwanda, Central Africa", "Rwanda", None),
        (
            "Las Flores, Marcala, La Paz, Honduras",
            "Honduras",
            "Las Flores, Marcala, La Paz",
        ),
        ("Ethiopia, East Africa", "Ethiopia", None),
    ],
)
def test_one_country_with_an_area_is_that_country(
    text: str, country: str, region: str | None
) -> None:
    assert origin(text) == OriginResult(country, region, ())


@pytest.mark.parametrize(
    ("text", "country"),
    [
        ("Yei, South Sudan", "South Sudan"),
        ("southern South Sudan", "South Sudan"),
        ("Belfast, Northern Ireland", "United Kingdom"),
        (
            "Kivu region of the Democratic Republic of the Congo",
            "Democratic Republic of the Congo",
        ),
        ("New South Wales, Australia", "Australia"),
    ],
)
def test_directions_and_of_never_switch_country(text: str, country: str) -> None:
    assert origin(text).country == country


@pytest.mark.parametrize(
    ("text", "region"),
    [
        (
            "Holualoa, North Kona growing district, Big Island of Hawai'i",
            "Holualoa, North Kona, Big Island",
        ),
        ("Boquete growing area, Chiriquí Province, Panama", "Boquete, Chiriquí"),
        ("Sidamo (also Sidama) growing region, southern Ethiopia", "Sidama"),
        ("Ethiopia Guji", "Guji"),
    ],
)
def test_region_has_no_leftover_words(text: str, region: str) -> None:
    assert origin(text).region == region


def test_no_alias_is_found_as_another_country() -> None:
    # Every spelling, looked up as an origin part on its own, must land on its
    # own country: stripping directions or taking its last words never switches
    # it ("north korea" isn't "korea", "south sudan" isn't "sudan").
    wrong = {}
    for alias, place in PLACES.items():
        if place.kind == "area":
            continue
        found = origin(alias).country
        if found not in (place.country, None):
            wrong[alias] = found
    assert wrong == {}


@pytest.mark.parametrize(
    "text",
    [
        "East, Central, and southern Africa",
        "Various Latin American origins.",
        "Espresso Blend",
        "Various Africa growing regions.",
    ],
)
def test_blends_named_by_an_area_inside_a_part_are_multiple(text: str) -> None:
    assert origin(text).country == "Multiple"


@pytest.mark.parametrize(
    ("text", "country"),
    [
        ("Apaneca Ilamatepec mountain range", "El Salvador"),
        ("San Cristobal, Galapagos Islands", "Ecuador"),
    ],
)
def test_named_region_inside_a_part(text: str, country: str) -> None:
    assert origin(text).country == country


def test_directions_before_of_stay_with_the_text_before() -> None:
    assert (
        origin(
            "Ka'u growing region, southwestern corner of the Big Island of Hawai'i"
        ).region
        == "Ka'u, southwestern corner, Big Island"
    )


def test_one_word_region_with_filler_is_not_split_out() -> None:
    # "Cauca growing region" is one word of name plus filler: not a named region
    # inside "Valle del Cauca", which must stay whole.
    assert (
        origin("Valle del Cauca growing region, Colombia").region == "Valle del Cauca"
    )


@pytest.mark.parametrize(
    "text",
    [
        "Indonesia; Central and South America",
        "Africa; Indonesia; South America",
        "Ethiopia; Central America",
        "Brazil; Southeast Asia",
        "East Africa and Java, Indonesia.",
        "Brazil, other South America origins, Central Africa.",
        "Honduras, other Central and South America",
    ],
)
def test_country_outside_the_area_is_a_blend(text: str) -> None:
    # An area is only context for a country inside it; otherwise, or with a
    # blend word ("other"), the coffee comes from more than one place.
    assert origin(text).country == "Multiple"


# --- Roaster locations ------------------------------------------------------------


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


@pytest.mark.parametrize(
    ("text", "region"),
    [
        ("Sacramento, Califonria", "California"),
        ("Seattle, Washingtn", "Washington"),
        ("Vancouver, British Colunbia", "British Columbia"),
    ],
)
def test_roaster_close_spelling(text: str, region: str) -> None:
    result = roaster(text)

    assert result.region == region
    assert result.rules == ("close_spelling",)


def test_roaster_without_comma() -> None:
    assert roaster("Scottsdale Arizona") == RoasterResult(
        "Scottsdale", "Arizona", "United States", ()
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


def test_reviewed_spellings_are_exact() -> None:
    # Confirmed from the first real clean's report: no longer close spellings.
    assert roaster("Sacramento, Calfornia") == RoasterResult(
        "Sacramento", "California", "United States", ()
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Hong Kong, China", (None, None, "Hong Kong")),
        ("Kowloon, Hong Kong, China", ("Kowloon", None, "Hong Kong")),
        ("Macao, China", (None, None, "Macao")),
        ("Vancouver, BC", ("Vancouver", "British Columbia", "Canada")),
        (
            "Burnaby, British Colombia, Canada",
            ("Burnaby", "British Columbia", "Canada"),
        ),
    ],
)
def test_roaster_middle_part(
    text: str, expected: tuple[str | None, str | None, str | None]
) -> None:
    city, region, country = expected

    assert roaster(text) == RoasterResult(city, region, country, ())


def test_roaster_west_virginia() -> None:
    assert roaster("Morgantown West Virginia").region == "West Virginia"


def test_city_named_like_a_country_is_not_the_country() -> None:
    # Armenia is a city in Colombia's coffee region.
    assert roaster("Armenia, Colombia") == RoasterResult(
        "Armenia", None, "Colombia", ()
    )


# --- On the tables ----------------------------------------------------------------


def test_clean_origin_joins_back() -> None:
    values = ["Antigua, Guatemala", "Antigua, Guatemala", "Not disclosed", None]

    result = run(
        lambda f: clean_origin(f, PLACES, load_growing_regions()),
        "coffee_origin",
        values,
    )

    assert result.height == 4
    assert result["origin_country"].to_list() == ["Guatemala", "Guatemala", None, None]
    assert result["origin_region"].to_list() == ["Antigua", "Antigua", None, None]
    assert result["_rule_coffee_origin"].to_list() == [[], [], ["not_disclosed"], []]
    assert result["slug"].to_list() == ["s0", "s1", "s2", "s3"]  # order kept


def test_clean_origin_adds_growing_region() -> None:
    regions = build_growing_regions(
        [
            ("Ethiopia", "Gedeb", "Yirgacheffe"),
            ("Ethiopia", "Yirgacheffe", "Yirgacheffe"),
        ]
    )
    values = ["Gedeb, Gedeo, Ethiopia", "Kayanza, Burundi", None]

    result = run(lambda f: clean_origin(f, PLACES, regions), "coffee_origin", values)

    assert result["origin_growing_region"].to_list() == ["Yirgacheffe", None, None]
    assert result["_rule_coffee_origin"].to_list() == [["growing_region"], [], []]


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
