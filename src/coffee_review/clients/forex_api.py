"""Client for the OpenExchangeRates API (JSON responses)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from coffee_review.clients.http import BaseHttpClient
from coffee_review.clients.reference import REFERENCE_RETRYABLE_STATUS, fetch_answer

if TYPE_CHECKING:
    from datetime import date

    import httpx

    from coffee_review.config import ForexApiSettings


class MissingApiKeyError(Exception):
    """Raised when the FX client is built without an API key configured."""

    def __init__(self) -> None:
        super().__init__(
            "No OpenExchangeRates key set. Add COFFEE_REVIEW_FOREX_API__API_KEY "
            "to your environment or .env file."
        )


class ForexApiClient(BaseHttpClient):
    """OpenExchangeRates: one day's rates per request."""

    retryable_status = REFERENCE_RETRYABLE_STATUS

    def __init__(
        self,
        settings: ForexApiSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if settings.api_key is None:
            raise MissingApiKeyError
        # The key goes in a header, not the ``app_id`` query parameter the API
        # also accepts: URLs are written to logs and error messages, headers aren't.
        super().__init__(
            settings,
            headers={
                "Accept": "application/json",
                "Authorization": f"Token {settings.api_key.get_secret_value()}",
            },
            transport=transport,
        )

    async def fetch_day(self, day: date) -> str:
        """That UTC day's rates, as the response text (see ``fetch_answer``)."""
        path = f"historical/{day:%Y-%m-%d}.json"
        return await fetch_answer(self, "openexchangerates", "GET", path)
