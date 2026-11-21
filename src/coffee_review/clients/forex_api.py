"""Client for the OpenExchangeRates API (JSON responses)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from coffee_review.clients.http import BaseHttpClient

if TYPE_CHECKING:
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
