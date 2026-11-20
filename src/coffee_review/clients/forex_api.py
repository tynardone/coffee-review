"""Client for forex API: JSON responses"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from coffee_review.clients.http import BaseHttpClient

if TYPE_CHECKING:
    import httpx

    from coffee_review.config import ForexApiSettings


class ForexApiClient(BaseHttpClient):
    def __init__(
        self,
        settings: ForexApiSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(
            settings, headers={"Accept": "application/json"}, transport=transport
        )
        # Some APIs want the key as a query parameter instead of a header.
        self._auth_params = {"api_key": settings.api_key.get_secret_value()}

    async def get(self, path: str, **kwargs: Any) -> httpx.Response:
        kwargs["params"] = {**self._auth_params, **(kwargs.get("params") or {})}
        return await super().get(path, **kwargs)
