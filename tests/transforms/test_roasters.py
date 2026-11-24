"""Tests for coffee_review.transforms.roasters: one name per roaster."""

from __future__ import annotations

import string

import pytest
from hypothesis import given
from hypothesis import strategies as st

from coffee_review.models.clean import CLEAN_RULES, RULE_DESCRIPTIONS
from coffee_review.transforms.roasters import normalise_city, roaster_key

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
