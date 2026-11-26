"""Tests for coffee_review.transforms.roasters: one name per roaster."""

from __future__ import annotations

import string
from typing import TYPE_CHECKING

import polars as pl
import pytest
from hypothesis import given
from hypothesis import strategies as st

from coffee_review.models.clean import CLEAN_RULES, RULE_DESCRIPTIONS
from coffee_review.transforms.roasters import (
    ConflictingListedNamesError,
    NameDecision,
    NameMap,
    Spelling,
    Thresholds,
    build_name_map,
    clean_roaster,
    normalise_city,
    roaster_key,
    roaster_spellings,
)
from tests.transforms.helpers import run

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

# --- The matching key ----------------------------------------------------------


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("JBC Coffee Roasters", "JBC"),
        ("El Gran Cafe", "El Gran Café"),
        ("PT's Coffee Roasting Co.", "PTs Coffee"),
        ("Temple Coffee Roasters", "Temple Coffee"),
        ("Starbucks Coffee", "STARBUCKS"),
        ("Bird & Bear", "Bird and Bear"),
    ],
)
def test_variants_share_a_key(a: str, b: str) -> None:
    assert roaster_key(a) == roaster_key(b)


def test_different_names_keep_different_keys() -> None:
    assert roaster_key("Heat Coffee") != roaster_key("Heart Coffee")


def test_all_filler_name_keeps_its_words() -> None:
    assert roaster_key("The Coffee Company") == "the coffee company"
    assert roaster_key("Coffee Co") == "coffee co"


@given(st.text())
def test_key_is_idempotent(name: str) -> None:
    assert roaster_key(roaster_key(name)) == roaster_key(name)


@given(st.text(alphabet=string.ascii_letters + string.digits + " "))
def test_key_ignores_case(name: str) -> None:  # ASCII: "ß".upper() is "SS"
    assert roaster_key(name.upper()) == roaster_key(name.lower())


def test_city_word_is_ignored() -> None:
    assert normalise_city("Taoyuan City") == normalise_city("taoyuan")


def test_city_ignores_accents_and_case() -> None:
    assert normalise_city("SÃO PAULO") == normalise_city("Sao Paulo")


def test_new_rules_have_descriptions() -> None:
    for rule in ("spelling_variant", "listed_name", "possible_match"):
        assert rule in CLEAN_RULES
        assert RULE_DESCRIPTIONS[rule]


# --- The name map ----------------------------------------------------------------

PDX = frozenset({("portland", "United States")})
SEATTLE = frozenset({("seattle", "United States")})
LA = frozenset({("los angeles", "United States")})
MADISON = frozenset({("madison", "United States")})
US = frozenset({("", "United States")})
AU = frozenset({("melbourne", "Australia")})
KE = frozenset({("nairobi", "Kenya")})
TOKYO = frozenset({("tokyo", "Japan")})
TAIPEI = frozenset({("taipei", "Taiwan")})


def S(
    name: str, reviews: int, places: frozenset[tuple[str, str]] = frozenset()
) -> Spelling:
    return Spelling(name, reviews, places)


def decide(
    *spellings: Spelling,
    listed: Mapping[str, str] | None = None,
    distinct: Sequence[tuple[str, str]] = (),
    thresholds: Thresholds = Thresholds(),  # noqa: B008
) -> Mapping[str, NameDecision]:
    return build_name_map(spellings, listed or {}, distinct, thresholds).decisions


def test_shared_key_merges_into_most_reviewed() -> None:
    d = decide(S("JBC Coffee Roasters", 5, MADISON), S("JBC", 2, MADISON))
    assert d["JBC"] == NameDecision("JBC Coffee Roasters", ("spelling_variant",))
    assert d["JBC Coffee Roasters"] == NameDecision("JBC Coffee Roasters", ())


def test_tie_keeps_first_alphabetically() -> None:
    d = decide(S("Temple Coffee Roasters", 2), S("Temple Coffee", 2))
    assert d["Temple Coffee Roasters"].name == "Temple Coffee"


def test_shared_key_across_countries_goes_to_review() -> None:
    d = decide(S("Coffee Alchemy", 3, AU), S("Alchemy", 2, US))
    assert d["Alchemy"] == NameDecision(
        "Alchemy", ("possible_match",), ("Coffee Alchemy",)
    )
    assert d["Coffee Alchemy"] == NameDecision("Coffee Alchemy", ())


def test_shared_key_splits_by_country() -> None:
    d = decide(
        S("Coffee Alchemy", 5, AU), S("Alchemy", 2, US), S("Alchemy Coffee", 1, US)
    )
    assert d["Alchemy Coffee"] == NameDecision("Alchemy", ("spelling_variant",))
    assert d["Alchemy"].rules == ("possible_match",)


def test_unknown_country_joins_most_reviewed_group() -> None:
    d = decide(S("Coffee Alchemy", 5, AU), S("Alchemy", 2, US), S("ALCHEMY", 1))
    assert d["ALCHEMY"] == NameDecision("Coffee Alchemy", ("spelling_variant",))


def test_very_close_spelling_merges() -> None:
    d = decide(S("La Mill", 4, LA), S("Lamill", 1, LA))
    assert d["Lamill"] == NameDecision("La Mill", ("close_spelling",))


def test_very_close_across_countries_goes_to_review() -> None:
    d = decide(S("La Mill", 4, LA), S("Lamill", 1, TOKYO))
    assert d["Lamill"] == NameDecision("Lamill", ("possible_match",), ("La Mill",))


def test_review_band_is_listed_not_merged() -> None:  # boyds / boyd: 0.889
    d = decide(S("Boyd Coffee", 5, PDX), S("Boyds", 2, PDX))
    assert d["Boyds"] == NameDecision("Boyds", ("possible_match",), ("Boyd Coffee",))


def test_review_band_across_countries_is_ignored() -> None:  # heat / heart: 0.889
    d = decide(S("Heart Coffee", 5, PDX), S("Heat Coffee", 2, TAIPEI))
    assert d["Heat Coffee"].rules == ()
    assert d["Heart Coffee"].rules == ()


def test_low_band_listed_in_the_same_city() -> None:  # press / express: 0.833
    d = decide(S("Coffee Express", 3, PDX), S("Press Coffee", 1, PDX))
    assert d["Press Coffee"].rules == ("possible_match",)


def test_low_band_ignored_in_different_cities() -> None:
    d = decide(S("Coffee Express", 3, PDX), S("Press Coffee", 1, SEATTLE))
    assert d["Press Coffee"].rules == ()


def test_short_keys_are_never_compared() -> None:
    low = Thresholds(merge_at=0.5, review_at=0.4, review_same_city_at=0.3)
    d = decide(S("JBC", 2, MADISON), S("JBD", 1, MADISON), thresholds=low)
    assert d["JBD"].rules == ()


def test_unknown_location_never_blocks() -> None:
    d = decide(S("La Mill", 4, LA), S("Lamill", 1))
    assert d["Lamill"] == NameDecision("La Mill", ("close_spelling",))


def test_chain_cannot_join_two_countries() -> None:  # every pair scores 0.833
    loose = Thresholds(merge_at=0.8, review_at=0.75, review_same_city_at=0.7)
    d = decide(
        S("abcdef", 5, US), S("abcdeg", 3), S("abcdeh", 1, TOKYO), thresholds=loose
    )
    assert d["abcdeg"].name == "abcdef"
    assert d["abcdeh"].name == "abcdeh"


def test_listed_row_merges_whatever_the_score() -> None:
    d = decide(
        S("Bold Bean", 2), S("BB Roasters", 1), listed={"BB Roasters": "Bold Bean"}
    )
    assert d["BB Roasters"] == NameDecision("Bold Bean", ("listed_name",))
    assert d["Bold Bean"] == NameDecision("Bold Bean", ())


def test_listed_row_overrides_country_conflict() -> None:
    d = decide(
        S("Roadmap CoffeeWorks", 3, US),
        S("Roadmap Coffee Works", 1, KE),
        listed={"Roadmap Coffee Works": "Roadmap CoffeeWorks"},
    )
    assert d["Roadmap Coffee Works"] == NameDecision(
        "Roadmap CoffeeWorks", ("listed_name",)
    )


def test_listed_row_names_the_group() -> None:
    d = decide(
        S("JBC", 5), S("JBC Coffee Roasters", 2), listed={"JBC": "JBC Coffee Roasters"}
    )
    assert d["JBC"] == NameDecision("JBC Coffee Roasters", ("listed_name",))
    assert d["JBC Coffee Roasters"] == NameDecision("JBC Coffee Roasters", ())


def test_listed_name_may_be_new() -> None:
    d = decide(S("JBC", 5), S("JBC Coffee Roasters", 2), listed={"Jbc": "JBC Coffee"})
    assert d["JBC"] == NameDecision("JBC Coffee", ("listed_name",))
    assert d["JBC Coffee Roasters"] == NameDecision("JBC Coffee", ("listed_name",))


def test_distinct_pair_is_never_merged_or_listed() -> None:
    d = decide(S("La Mill", 4, LA), S("Lamill", 1), distinct=[("Lamill", "La Mill")])
    assert d["Lamill"] == NameDecision("Lamill", ())


def test_distinct_pair_settles_a_shared_key_review() -> None:
    d = decide(
        S("Coffee Alchemy", 3, AU),
        S("Alchemy", 2, US),
        distinct=[("Alchemy", "Coffee Alchemy")],
    )
    assert d["Alchemy"] == NameDecision("Alchemy", ())


def test_unused_listed_row_is_reported() -> None:
    names = build_name_map(
        [S("La Mill", 1)],
        {"Gone Roasters": "X"},
        [("La Mill", "Vanished")],
        Thresholds(),
    )
    assert names.unused_rows == ("Gone Roasters", "Vanished")


def test_several_candidates_are_all_listed() -> None:  # every pair scores 0.8
    t = Thresholds(merge_at=0.95, review_at=0.8, review_same_city_at=0.7)
    d = decide(S("abcdx", 5), S("abcdy", 4), S("abcde", 1), thresholds=t)
    assert d["abcde"].candidates == ("abcdx", "abcdy")
    assert d["abcdy"].candidates == ("abcdx",)


def test_spelling_with_no_name_is_skipped() -> None:
    assert decide(S("", 3)) == {}


def test_a_score_exactly_at_a_threshold_counts() -> None:  # abcd / abcdef: 0.8
    d = decide(S("Abcdef", 3, PDX), S("Abcd", 1, PDX))
    assert d["Abcd"].rules == ("possible_match",)


# --- From the tables, and back ---------------------------------------------------


def test_spellings_count_reviews_and_places() -> None:
    roasted = pl.DataFrame(
        {
            "raw_roaster": ["JBC", "JBC", "Temple"],
            "roaster_city": ["Madison", "Madison City", None],
            "roaster_country": ["United States", "United States", None],
        }
    )
    green = pl.DataFrame({"raw_roaster": ["JBC"]})
    found = {s.name: s for s in roaster_spellings(roasted, green)}
    assert found["JBC"] == Spelling("JBC", 3, frozenset({("madison", "United States")}))
    assert found["Temple"] == Spelling("Temple", 1)


def test_spellings_skip_blank_names() -> None:
    frame = pl.DataFrame({"raw_roaster": [None, "", "  "]})
    assert roaster_spellings(frame) == []


NAMES = NameMap(
    {
        "JBC": NameDecision("JBC Coffee Roasters", ("spelling_variant",)),
        "JBC Coffee Roasters": NameDecision("JBC Coffee Roasters", ()),
    },
    (),
)


def test_clean_roaster_applies_decisions() -> None:
    out = run(
        lambda f: clean_roaster(f, NAMES), "roaster", ["JBC", "JBC Coffee Roasters"]
    )
    assert out["roaster"].to_list() == ["JBC Coffee Roasters", "JBC Coffee Roasters"]
    assert out["_rule_roaster"].to_list() == [["spelling_variant"], []]


def test_unknown_spelling_is_kept() -> None:
    out = run(lambda f: clean_roaster(f, NAMES), "roaster", ["Temple"])
    assert out["roaster"].to_list() == ["Temple"]
    assert out["_rule_roaster"].to_list() == [[]]


def test_null_roaster_is_left_alone() -> None:
    out = run(lambda f: clean_roaster(f, NAMES), "roaster", [None, ""])
    assert out["roaster"].to_list() == [None, ""]
    assert out["_rule_roaster"].to_list() == [[], []]


def test_clean_roaster_keeps_row_order() -> None:
    names = ["JBC Coffee Roasters", "Temple", "JBC", None]
    out = run(lambda f: clean_roaster(f, NAMES), "roaster", names)
    assert out["slug"].to_list() == ["s0", "s1", "s2", "s3"]


def test_non_latin_names_keep_distinct_keys() -> None:
    assert roaster_key("黑貓咖啡") != roaster_key("山頂咖啡")
    assert roaster_key("黑貓咖啡") != roaster_key("---")
    assert roaster_key("黑貓咖啡")


def test_non_latin_names_are_not_merged() -> None:
    d = decide(S("黑貓咖啡", 5, TAIPEI), S("山頂咖啡", 1))
    assert d["山頂咖啡"] == NameDecision("山頂咖啡", ())


def test_conflicting_listed_rows_stop_the_run() -> None:
    with pytest.raises(ConflictingListedNamesError, match="JBC Coffee Roasters"):
        decide(
            S("JBC", 2),
            S("JBC Coffee", 1),
            listed={"JBC": "JBC Coffee Roasters", "jbc coffee": "JBC Roasters"},
        )


def test_listed_name_that_is_another_rows_key_stops_the_run() -> None:
    with pytest.raises(ConflictingListedNamesError):
        decide(
            S("Hula Daddy", 2),
            S("Kona Daddy", 1),
            listed={"Hula Daddy": "Hula Daddy Kona Coffee", "Kona Daddy": "Hula Daddy"},
        )


def test_rows_naming_the_same_roaster_agree() -> None:
    d = decide(
        S("Bold Bean", 2),
        S("BB Roasters", 1),
        S("B.B.", 1),
        listed={"BB Roasters": "Bold Bean", "B.B.": "Bold Bean"},
    )
    assert {d[n].name for n in d} == {"Bold Bean"}


def test_row_still_renaming_is_not_unused() -> None:  # its roaster's key is in the data
    names = build_name_map([S("JBC", 1)], {"Ghost": "JBC Roasters"}, [], Thresholds())
    assert names.decisions["JBC"].name == "JBC Roasters"
    assert names.unused_rows == ()


def test_distinct_row_with_one_side_gone_is_unused() -> None:
    names = build_name_map([S("La Mill", 1)], {}, [("La Mill", "Vanished")])
    assert names.unused_rows == ("Vanished",)


def test_shared_key_distinct_row_settles_only_its_pair() -> None:
    d = decide(
        S("Coffee Alchemy", 5, AU),
        S("Alchemy", 2, US),
        S("Alchemy Coffee Co", 1, TOKYO),
        distinct=[("Alchemy", "Coffee Alchemy")],
    )
    assert d["Alchemy"].rules == ()
    assert d["Alchemy Coffee Co"].rules == ("possible_match",)


def test_generic_names_are_reviewed_not_merged() -> None:
    d = decide(S("The Coffee Roaster", 3), S("The Coffee Roasters", 1))
    assert d["The Coffee Roasters"] == NameDecision(
        "The Coffee Roasters", ("possible_match",), ("The Coffee Roaster",)
    )


def test_close_spelling_joins_a_listed_group_under_its_listed_name() -> None:
    d = decide(
        S("Bold Bea", 5),  # most reviews, but joins by close spelling
        S("Bold Bean", 1),
        S("BB Roasters", 1),
        listed={"BB Roasters": "Bold Bean Co"},
    )
    assert d["Bold Bea"] == NameDecision("Bold Bean Co", ("close_spelling",))
    assert d["Bold Bean"] == NameDecision("Bold Bean Co", ("listed_name",))


def test_groups_listed_under_different_names_stay_apart() -> None:  # 0.923
    d = decide(
        S("La Mill", 2, LA),
        S("Lamill", 1, LA),
        listed={"La Mill": "La Mill Coffee", "Lamill": "Lamill Roasters"},
    )
    assert d["La Mill"] == NameDecision("La Mill Coffee", ("listed_name",))
    assert d["Lamill"] == NameDecision("Lamill Roasters", ("listed_name",))


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Großmann Kaffee", "Grossmann Kaffee"),
        ("Østerbro Kaffe", "Osterbro Kaffe"),
        ("Łódź Roasters", "Lodz Roasters"),
        ("Kaffæ", "Kaffae"),
    ],
)
def test_latin_letters_without_accents_are_spelled_out(a: str, b: str) -> None:
    assert roaster_key(a) == roaster_key(b)


def test_spellings_from_no_frames_is_empty() -> None:
    assert roaster_spellings() == []


def test_key_of_styled_letters_is_stable() -> None:  # NFKD turns 𝑨 into A
    assert roaster_key(roaster_key(":𝑨")) == roaster_key(":𝑨") == "a"


def test_rows_giving_one_new_name_join_their_groups() -> None:
    d = decide(
        S("Kafe Alpha", 2, AU),
        S("Kafe Alpa", 1, US),
        listed={"Kafe Alpha": "Alpa Brand", "Kafe Alpa": "Alpa Brand"},
    )
    assert d["Kafe Alpha"] == NameDecision("Alpa Brand", ("listed_name",))
    assert d["Kafe Alpa"] == NameDecision("Alpa Brand", ("listed_name",))
