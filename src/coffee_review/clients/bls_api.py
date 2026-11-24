"""Client for the BLS Public Data API, keyless (v1).

Keyless means 25 requests a day and at most 10 years per request; the pipeline
plans requests within that. BLS reports its daily limit inside a 200 response
(status ``REQUEST_NOT_PROCESSED``), so that's checked here and raised as a
refusal, never passed on as an answer.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from coffee_review.clients.economic import (
    ECONOMIC_RETRYABLE_STATUS,
    SourceRefusedError,
    fetch_answer,
)
from coffee_review.clients.http import BaseHttpClient

if TYPE_CHECKING:
    import httpx

    from coffee_review.config import BLSApiSettings

SOURCE = "bls"
NOT_PROCESSED = "REQUEST_NOT_PROCESSED"


class BlsApiClient(BaseHttpClient):
    retryable_status = ECONOMIC_RETRYABLE_STATUS

    def __init__(
        self,
        settings: BLSApiSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(
            settings, headers={"Accept": "application/json"}, transport=transport
        )

    async def fetch_years(self, series: str, start: int, end: int) -> str:
        """``series`` for the years ``start``–``end``, as the response text."""
        body = {"seriesid": [series], "startyear": str(start), "endyear": str(end)}
        text = await fetch_answer(self, SOURCE, "POST", "timeseries/data/", json=body)
        _raise_if_refused(text)
        return text


def _raise_if_refused(text: str) -> None:
    try:
        body = json.loads(text)
    except json.JSONDecodeError:
        return  # not ours to judge: the parser rejects it
    if isinstance(body, dict) and body.get("status") == NOT_PROCESSED:
        raise SourceRefusedError(f"{SOURCE}: {NOT_PROCESSED} {body.get('message')}")
