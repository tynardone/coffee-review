"""Parse an OpenExchangeRates ``historical/YYYY-MM-DD.json`` response.

A 200 response is ``{disclaimer, license, timestamp, base, rates}``, with
``rates`` mapping ISO codes to units per one ``base``. The free plan's base is
always USD; anything else is refused rather than stored as "per USD". A 400
``not_available`` answer means the source has no rates for that day: a gap.
"""

from __future__ import annotations

import json
import math
import re
from typing import TYPE_CHECKING, Any

from coffee_review.models.economic import (
    EconomicParseError,
    FxParsed,
    FxRate,
    ParseProblem,
)

if TYPE_CHECKING:
    from datetime import date

CURRENCY_CODE = re.compile(r"^[A-Z]{3}$")
GAP_MESSAGES = frozenset({"not_available"})


def parse_openexchangerates(text: str, day: date) -> FxParsed:
    """Parse one day's OpenExchangeRates response.

    Args:
        text: The response as received.
        day: The day it's for.

    Returns:
        The day's rates (bad ones dropped and recorded), or the gap it reports.

    Raises:
        EconomicParseError: If it isn't a USD-based answer or a ``not_available``.
    """
    body = _load(text)
    if body.get("error") is True:
        message = body.get("message")
        if message in GAP_MESSAGES:
            return FxParsed(day=day, rates=(), gap=str(message))
        raise EconomicParseError(f"fx {day}: error response {message!r}")
    if body.get("base") != "USD":
        raise EconomicParseError(
            f"fx {day}: base is {body.get('base')!r}, expected 'USD'"
        )
    rates = body.get("rates")
    if not isinstance(rates, dict):
        raise EconomicParseError(f"fx {day}: no rates object")

    kept: list[FxRate] = []
    problems: list[ParseProblem] = []
    for code, value in rates.items():
        reason = _problem(code, value)
        if reason is None:
            kept.append(FxRate(day=day, currency=code, units_per_usd=float(value)))
        else:
            where = f"fx {day}"
            problems.append(
                ParseProblem(where=where, value=f"{code}={value}", reason=reason)
            )
    return FxParsed(day=day, rates=tuple(kept), problems=tuple(problems))


def _load(text: str) -> dict[str, Any]:
    try:
        body = json.loads(text)
    except json.JSONDecodeError as exc:
        raise EconomicParseError(f"not JSON: {exc}") from None
    if not isinstance(body, dict):
        raise EconomicParseError("not a JSON object")
    return body


def _problem(code: object, value: object) -> str | None:
    """Why this rate can't be kept, or None if it can."""
    if not isinstance(code, str) or not CURRENCY_CODE.match(code):
        return "not an ISO currency code"
    if isinstance(value, bool) or not isinstance(value, int | float):
        return "not a number"
    if not math.isfinite(value) or value <= 0:
        return "not a positive number"
    return None
