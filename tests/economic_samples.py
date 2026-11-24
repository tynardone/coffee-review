"""Hand-written OpenExchangeRates and BLS responses, and the real clients over
a fake network (``httpx.MockTransport``), for the economic-stage tests.

The bodies follow each API's documented shape; none is a real response.
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import TYPE_CHECKING

import httpx
from pydantic import SecretStr

from coffee_review.clients.bls_api import BlsApiClient
from coffee_review.clients.forex_api import ForexApiClient
from coffee_review.config import BLSApiSettings, ForexApiSettings

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

SERIES = "CUUR0000SA0"
NOT_AVAILABLE = json.dumps({"error": True, "status": 400, "message": "not_available"})
FAST = {
    "rate_per_sec": 1000,
    "max_retries": 1,
    "backoff_base_seconds": 0.001,
    "backoff_max_seconds": 0.001,
}


def oxr_body(rates: dict[str, float] | None = None, base: str = "USD") -> str:
    return json.dumps(
        {
            "disclaimer": "Usage subject to terms",
            "license": "https://openexchangerates.org/license",
            "timestamp": 0,
            "base": base,
            "rates": rates or {"USD": 1, "TWD": 30.0},
        }
    )


def bls_body(
    months: Iterable[date],
    *,
    unavailable: Iterable[date] = (),
    value: float = 100.0,
    message: Iterable[str] = (),
    series: str = SERIES,
) -> str:
    def row(month: date, text: str, notes: list[dict[str, str]]) -> dict[str, object]:
        return {
            "year": str(month.year),
            "period": f"M{month.month:02d}",
            "periodName": "x",
            "value": text,
            "footnotes": notes,
        }

    data = [row(m, str(value), [{}]) for m in months]
    data += [
        row(m, "-", [{"code": "X", "text": "Data unavailable"}]) for m in unavailable
    ]
    return json.dumps(
        {
            "status": "REQUEST_SUCCEEDED",
            "responseTime": 1,
            "message": list(message),
            "Results": {"series": [{"seriesID": series, "data": data}]},
        }
    )


type Reply = str | httpx.Response


def _response(reply: Reply) -> httpx.Response:
    return (
        reply if isinstance(reply, httpx.Response) else httpx.Response(200, text=reply)
    )


def fx_client(
    answer: Callable[[date], Reply],
) -> tuple[ForexApiClient, list[date]]:
    """A real ForexApiClient whose requests are answered by ``answer(day)``;
    the list records every day asked for."""
    asked: list[date] = []

    def handler(request: httpx.Request) -> httpx.Response:
        match = re.search(r"historical/(\d{4}-\d{2}-\d{2})\.json$", request.url.path)
        assert match is not None, request.url
        day = date.fromisoformat(match.group(1))
        asked.append(day)
        return _response(answer(day))

    settings = ForexApiSettings(api_key=SecretStr("test-key"), **FAST)
    return ForexApiClient(settings, transport=httpx.MockTransport(handler)), asked


def bls_client(
    answer: Callable[[int, int], Reply],
) -> tuple[BlsApiClient, list[tuple[int, int]]]:
    """A real BlsApiClient whose requests are answered by
    ``answer(start_year, end_year)``; the list records every request."""
    asked: list[tuple[int, int]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        years = (int(body["startyear"]), int(body["endyear"]))
        asked.append(years)
        return _response(answer(*years))

    settings = BLSApiSettings(**FAST)
    return BlsApiClient(settings, transport=httpx.MockTransport(handler)), asked


def published_through(
    latest: date, unavailable: Iterable[date] = ()
) -> Callable[[int, int], Reply]:
    """BLS answering every month up to ``latest``, with ``unavailable`` marked X."""
    gone = set(unavailable)

    def answer(start: int, end: int) -> Reply:
        months = [
            date(year, n, 1) for year in range(start, end + 1) for n in range(1, 13)
        ]
        return bls_body(
            [m for m in months if m <= latest and m not in gone],
            unavailable=[m for m in months if m in gone],
        )

    return answer
