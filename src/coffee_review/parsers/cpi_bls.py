"""Parse a BLS Public Data API ``timeseries/data`` response.

The shape is ``{status, message, Results: {series: [{seriesID, data: [...]}]}}``
with each data row ``{year, period, periodName, value, footnotes}`` and
``value`` as text. Periods ``M01``–``M12`` are months; ``M13`` (the annual
average) and anything else are not, and are dropped. A value of ``-`` with
footnote ``X`` is the BLS saying the month is unavailable (October 2025: no
data was collected during the 2025 lapse in appropriations): a gap, never
asked for again.

BLS can answer ``REQUEST_SUCCEEDED`` with no data and the reason in
``message``: that's an answer with no months, not an error.
"""

from __future__ import annotations

import json
import math
import re
from datetime import date
from typing import Any

from coffee_review.models.economic import (
    CpiMonth,
    CpiParsed,
    EconomicParseError,
    ParseProblem,
)

MONTH_PERIOD = re.compile(r"^M(0[1-9]|1[0-2])$")
UNAVAILABLE = "X"


def parse_cpi_bls(text: str, series: str) -> CpiParsed:
    """Parse one BLS response.

    Args:
        text: The response as received.
        series: The series to read; any other series in the response is ignored.

    Returns:
        The series' months, the months the BLS marks unavailable, any messages,
        and values dropped as bad.

    Raises:
        EconomicParseError: If the response isn't a successful BLS answer.
    """
    body = _load(text)
    if body.get("status") != "REQUEST_SUCCEEDED":
        raise EconomicParseError(
            f"cpi: status {body.get('status')!r}, message {body.get('message')!r}"
        )
    results = body.get("Results")
    entries = results.get("series") if isinstance(results, dict) else None
    if not isinstance(entries, list):
        raise EconomicParseError("cpi: no Results.series list")

    months: list[CpiMonth] = []
    problems: list[ParseProblem] = []
    unavailable: list[date] = []
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("seriesID") != series:
            continue
        for row in entry.get("data") or []:
            month = _month(row)
            if month is None:
                continue  # M13 and other non-month periods
            value = str(row.get("value", ""))
            index = _index(value)
            said = _is_unavailable(row)
            if index is None and said:
                unavailable.append(month)
                continue
            if index is None:
                where = f"cpi {series} {month:%Y-%m}"
                reason = "not a positive number"
                problems.append(ParseProblem(where=where, value=value, reason=reason))
                continue
            months.append(CpiMonth(month=month, series=series, index_value=index))
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
        raise EconomicParseError(f"not JSON: {exc}") from None
    if not isinstance(body, dict):
        raise EconomicParseError("not a JSON object")
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


def _is_unavailable(row: dict[str, Any]) -> bool:
    """True when the BLS marks the month unavailable (footnote X)."""
    notes = row.get("footnotes") or []
    return any(isinstance(n, dict) and n.get("code") == UNAVAILABLE for n in notes)
