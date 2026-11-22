"""Tests for coffee_review.transforms.text."""

from __future__ import annotations

from coffee_review.transforms.text import clean_text_sections
from tests.transforms.helpers import run


def sections(notes: list[str | None]) -> tuple[list[str | None], list[list[str]]]:
    n = len(notes)
    result = run(
        clean_text_sections,
        "notes",
        notes,
        raw_blind_assessment=[None] * n,
        raw_bottom_line=[None] * n,
    )
    return result["notes"].to_list(), result["_rule_notes"].to_list()


def test_zero_width_removed() -> None:
    text = "a\u200bb‌‍c﻿"

    assert sections([text]) == (["abc"], [["zero_width_removed"]])


def test_plain_text_unchanged() -> None:
    assert sections(["ab", None]) == (["ab", None], [[], []])
