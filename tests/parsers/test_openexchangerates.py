"""Tests for coffee_review.parsers.openexchangerates.

Samples are hand-written in the documented shape, never real responses.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from coffee_review.models.economic import EconomicParseError
from coffee_review.parsers.openexchangerates import parse_openexchangerates

DAY = date(2020, 1, 1)


def oxr(base: str = "USD", rates: dict[str, object] | None = None) -> str:
    return json.dumps(
        {
            "disclaimer": "Usage subject to terms",
            "license": "https://openexchangerates.org/license",
            "timestamp": 1577836800,
            "base": base,
            "rates": {"USD": 1} if rates is None else rates,
        }
    )


def test_reads_every_rate() -> None:
    parsed = parse_openexchangerates(
        oxr(rates={"USD": 1, "TWD": 31.2, "CAD": 1.35}), DAY
    )

    assert {(r.currency, r.units_per_usd) for r in parsed.rates} == {
        ("USD", 1.0),
        ("TWD", 31.2),
        ("CAD", 1.35),
    }
    assert all(r.day == DAY for r in parsed.rates)
    assert parsed.day == DAY
    assert parsed.gap is None
    assert parsed.problems == ()


def test_rejects_a_non_usd_base() -> None:
    with pytest.raises(EconomicParseError, match="base"):
        parse_openexchangerates(oxr(base="EUR"), DAY)


def test_drops_a_zero_rate_with_a_problem() -> None:
    parsed = parse_openexchangerates(oxr(rates={"USD": 1, "VEF": 0}), DAY)

    assert [r.currency for r in parsed.rates] == ["USD"]
    assert parsed.problems[0].value == "VEF=0"


@pytest.mark.parametrize(
    "rates",
    [
        pytest.param({"USD": 1, "usd": 1.0}, id="lowercase-code"),
        pytest.param({"USD": 1, "TWD": "31"}, id="string-value"),
        pytest.param({"USD": 1, "TWD": None}, id="null-value"),
        pytest.param({"USD": 1, "TWD": True}, id="bool-value"),
        pytest.param({"USD": 1, "TWD": -2.5}, id="negative"),
    ],
)
def test_drops_each_bad_rate(rates: dict[str, object]) -> None:
    parsed = parse_openexchangerates(oxr(rates=rates), DAY)

    assert [r.currency for r in parsed.rates] == ["USD"]
    assert len(parsed.problems) == 1


def test_not_available_is_a_gap() -> None:
    body = json.dumps(
        {
            "error": True,
            "status": 400,
            "message": "not_available",
            "description": "Historical rates for the requested date are not available",
        }
    )

    parsed = parse_openexchangerates(body, DAY)

    assert parsed.gap == "not_available"
    assert parsed.rates == ()


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("", id="empty"),
        pytest.param("{", id="truncated"),
        pytest.param("[]", id="not-an-object"),
        pytest.param(
            json.dumps({"error": True, "status": 401, "message": "invalid_app_id"}),
            id="other-error",
        ),
        pytest.param(json.dumps({"base": "USD"}), id="no-rates"),
        pytest.param(json.dumps({"base": "USD", "rates": []}), id="rates-not-object"),
    ],
)
def test_rejects_what_isnt_an_answer(text: str) -> None:
    with pytest.raises(EconomicParseError):
        parse_openexchangerates(text, DAY)
