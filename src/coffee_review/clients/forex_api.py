"""Client for the OpenExchangeRates API (JSON responses)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

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
        super().__init__(
            settings, headers={"Accept": "application/json"}, transport=transport
        )
        # OpenExchangeRates takes the key as the ``app_id`` query parameter.
        self._auth_params = {"app_id": settings.api_key.get_secret_value()}

    async def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Send a request with ``app_id`` added to the caller's query params."""
        kwargs["params"] = {**(kwargs.get("params") or {}), **self._auth_params}
        return await super().request(method, path, **kwargs)
