"""Parse a BLS Public Data API ``timeseries/data`` response.

The shape is ``{status, message, Results: {series: [{seriesID, data: [...]}]}}``
with each data row ``{year, period, periodName, value, footnotes}`` and
``value`` as text. Periods ``M01``–``M12`` are months; ``M13`` (the annual
average) and anything else are not, and are dropped. A footnote coded ``P``
marks a value preliminary; a value of ``-`` with footnote ``X`` is the BLS
saying the month is unavailable (October 2025: no data was collected during
the 2025 lapse in appropriations), which becomes a gap in its own words.

BLS can answer ``REQUEST_SUCCEEDED`` with no data and the reason in
``message``: that's an answer with no months, not an error.
"""

from __future__ import annotations

import json
import math
import re
from datetime import date
from typing import Any

from coffee_review.models.reference import (
    CpiMonth,
    CpiParsed,
    CpiUnavailable,
    ParseProblem,
    ReferenceParseError,
)

MONTH_PERIOD = re.compile(r"^M(0[1-9]|1[0-2])$")
PRELIMINARY = "P"
UNAVAILABLE = "X"


def parse_cpi_bls(text: str, series: str) -> CpiParsed:
    """The months of ``series`` in one response."""
    body = _load(text)
    if body.get("status") != "REQUEST_SUCCEEDED":
        raise ReferenceParseError(
            f"cpi: status {body.get('status')!r}, message {body.get('message')!r}"
        )
    results = body.get("Results")
    entries = results.get("series") if isinstance(results, dict) else None
    if not isinstance(entries, list):
        raise ReferenceParseError("cpi: no Results.series list")

    months: list[CpiMonth] = []
    problems: list[ParseProblem] = []
    unavailable: list[CpiUnavailable] = []
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("seriesID") != series:
            continue
        for row in entry.get("data") or []:
            month = _month(row)
            if month is None:
                continue  # M13 and other non-month periods
            value = str(row.get("value", ""))
            index = _index(value)
            said = _unavailable(row)
            if index is None and said is not None:
                unavailable.append(
                    CpiUnavailable(month=month, series=series, said=said)
                )
                continue
            if index is None:
                where = f"cpi {series} {month:%Y-%m}"
                reason = "not a positive number"
                problem = ParseProblem(
                    where=where, value=value, reason=reason, period=month
                )
                problems.append(problem)
                continue
            months.append(
                CpiMonth(
                    month=month, series=series, index_value=index, final=_final(row)
                )
            )
    return CpiParsed(
        series=series,
        months=tuple(months),
        problems=tuple(problems),
        unavailable=tuple(unavailable),
        messages=_messages(body),
    )


def _load(text: str) -> dict[str, Any]:
    try:
        body = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ReferenceParseError(f"not JSON: {exc}") from None
    if not isinstance(body, dict):
        raise ReferenceParseError("not a JSON object")
    return body


def _messages(body: dict[str, Any]) -> tuple[str, ...]:
    """BLS's notes on the request: an empty list when all went well."""
    message = body.get("message") or []
    if isinstance(message, str):
        message = [message]
    return tuple(str(m) for m in message if m)


def _month(row: object) -> date | None:
    if not isinstance(row, dict):
        return None
    period = MONTH_PERIOD.match(str(row.get("period", "")))
    year = str(row.get("year", ""))
    if period is None or not year.isdigit():
        return None
    return date(int(year), int(period.group(1)), 1)


def _index(value: str) -> float | None:
    try:
        number = float(value)
    except ValueError:
        return None
    return number if math.isfinite(number) and number > 0 else None


def _final(row: dict[str, Any]) -> bool:
    notes = row.get("footnotes") or []
    return not any(
        isinstance(note, dict) and note.get("code") == PRELIMINARY for note in notes
    )


def _unavailable(row: dict[str, Any]) -> str | None:
    """The footnote's text when the BLS marks the month unavailable, else None."""
    for note in row.get("footnotes") or []:
        if isinstance(note, dict) and note.get("code") == UNAVAILABLE:
            return str(note.get("text") or "") or "unavailable (BLS footnote X)"
    return None
