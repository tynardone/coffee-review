"""Tests for coffee_review.parsers.value_shape."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from coffee_review.parsers.value_shape import value_shape


@pytest.mark.parametrize(
    ("text", "shape"),
    [
        ("$34.00/12 ounces", "$99.99/99 aaaaaa"),
        ("58/80", "99/99"),
        ("NA", "aa"),
        ("Kaʻū", "aaaa"),  # ʻ is a Unicode letter (the ʻokina)
        ("", ""),
    ],
)
def test_value_shape(text: str, shape: str) -> None:
    assert value_shape(text) == shape


@given(st.text())
def test_value_shape_properties(text: str) -> None:
    shape = value_shape(text)

    assert len(shape) == len(text)
    for original, shaped in zip(text, shape, strict=True):
        assert shaped in {"9", "a"} or shaped == original
