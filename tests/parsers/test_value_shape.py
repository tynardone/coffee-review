"""Tests for coffee_review.parsers.value_shape."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from coffee_review.parsers.value_shape import value_shape


@pytest.mark.parametrize(
    ("text", "shape"),
    [
        ("$34.00/12 ounces", "$99.99/99 a"),
        ("58/80", "99/99"),
        ("NA", "a"),
        ("November 2011", "a 9999"),
        ("June 2003", "a 9999"),
        ("Antigua, Guatemala", "a, a"),
        ("Kaʻū", "a"),  # ʻ is a Unicode letter (the ʻokina)
        ("", ""),
    ],
)
def test_value_shape(text: str, shape: str) -> None:
    assert value_shape(text) == shape


@given(st.text())
def test_value_shape_keeps_every_digit(text: str) -> None:
    assert value_shape(text).count("9") == sum(char.isdigit() for char in text)


@given(st.text())
def test_value_shape_letters_collapse_to_single_a(text: str) -> None:
    shape = value_shape(text)

    assert "aa" not in shape
    assert all(char == "a" for char in shape if char.isalpha())


@given(st.text())
def test_value_shape_is_stable(text: str) -> None:
    assert value_shape(value_shape(text)) == value_shape(text)
