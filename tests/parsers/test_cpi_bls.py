"""Tests for coffee_review.parsers.cpi_bls.

Samples are hand-written in the documented shape, never real responses.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from coffee_review.models.reference import ReferenceParseError
from coffee_review.parsers.cpi_bls import parse_cpi_bls

SERIES = "CUUR0000SA0"


def bls(
    rows: list[tuple[str, str, str]],
    *,
    footnote: str | None = None,
    series: str = SERIES,
    status: str = "REQUEST_SUCCEEDED",
    message: list[str] | None = None,
) -> str:
    notes = [{"code": footnote, "text": "Preliminary."}] if footnote else [{}]
    data = [
        {
            "year": year,
            "period": period,
            "periodName": "x",
            "value": value,
            "footnotes": notes,
        }
        for year, period, value in rows
    ]
    return json.dumps(
        {
            "status": status,
            "responseTime": 30,
            "message": message or [],
            "Results": {"series": [{"seriesID": series, "data": data}]},
        }
    )


def test_reads_months_and_drops_m13() -> None:
    parsed = parse_cpi_bls(
        bls([("2020", "M01", "257.971"), ("2020", "M13", "258.811")]), SERIES
    )

    assert [(m.month, m.index_value) for m in parsed.months] == [
        (date(2020, 1, 1), 257.971)
    ]
    assert parsed.months[0].series == SERIES
    assert parsed.months[0].final is True
    assert parsed.problems == ()


def test_reads_december() -> None:
    parsed = parse_cpi_bls(bls([("2019", "M12", "256.974")]), SERIES)

    assert parsed.months[0].month == date(2019, 12, 1)


def test_drops_other_periods_silently() -> None:
    parsed = parse_cpi_bls(bls([("2020", "S01", "1"), ("2020", "A01", "1")]), SERIES)

    assert parsed.months == ()
    assert parsed.problems == ()


def test_preliminary_is_not_final() -> None:
    parsed = parse_cpi_bls(bls([("2020", "M02", "258.678")], footnote="P"), SERIES)

    assert parsed.months[0].final is False


@pytest.mark.parametrize("value", ["-", "", "0", "-1.5", "nan"])
def test_bad_value_is_a_problem(value: str) -> None:
    parsed = parse_cpi_bls(
        bls([("2020", "M01", value), ("2020", "M02", "258.678")]), SERIES
    )

    assert [m.month for m in parsed.months] == [date(2020, 2, 1)]
    assert parsed.problems[0].value == value


def test_other_series_ignored() -> None:
    parsed = parse_cpi_bls(bls([("2020", "M01", "1")], series="OTHER"), SERIES)

    assert parsed.months == ()


def test_empty_data_with_message_is_no_months() -> None:
    parsed = parse_cpi_bls(bls([], message=["No Data Available for Series"]), SERIES)

    assert parsed.months == ()


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(bls([], status="REQUEST_FAILED"), id="failed"),
        pytest.param("", id="empty"),
        pytest.param("{", id="truncated"),
        pytest.param(json.dumps({"status": "REQUEST_SUCCEEDED"}), id="no-results"),
        pytest.param(
            json.dumps({"status": "REQUEST_SUCCEEDED", "Results": {"series": {}}}),
            id="series-not-list",
        ),
    ],
)
def test_rejects_what_isnt_an_answer(text: str) -> None:
    with pytest.raises(ReferenceParseError):
        parse_cpi_bls(text, SERIES)
