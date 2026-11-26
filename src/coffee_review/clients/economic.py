"""How the economic-data clients tell an answer from a refusal.

A client returns the response text whenever the source *answered*, including
"I have no data for that" (the parser decides what the answer means). When
the source didn't answer, it raises one of three errors, and the pipeline
stops cleanly:

- ``SourceAuthError``: the key is missing or wrong (401).
- ``SourceRefusedError``: the source said no: quota, over-use, permission
  (403, 429, or a refusal inside a 200, like the BLS daily limit).
- ``SourceUnavailableError``: no usable answer after retries (5xx, timeouts,
  network), or a status that isn't an answer.

Shared by ``openexchangerates.py`` and ``bls.py``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import httpx

from coffee_review.clients.http import RetriesExhaustedError

if TYPE_CHECKING:
    from coffee_review.clients.http import BaseHttpClient

# Retried: a server or gateway that may recover. Not retried: 429, which here
# means "you've used your allowance", not "slow down for a moment".
ECONOMIC_RETRYABLE_STATUS: frozenset[int] = frozenset({408, 500, 502, 503, 504})
ANSWER_STATUS = frozenset({200, 400})


class SourceAuthError(Exception):
    """The source rejected the key, or there was none."""


class SourceRefusedError(Exception):
    """The source refused: quota, over-use or permission."""


class SourceUnavailableError(Exception):
    """The source gave no usable answer, even after retries."""


async def fetch_answer(
    client: BaseHttpClient, source: str, method: str, path: str, **kwargs: Any
) -> str:
    """Send a request and return the text if the source answered.

    A 200 or 400 is an answer ("no data for that" included); anything else
    raises one of the three source errors.

    Args:
        client: The open client to send through.
        source: The source's name, for error messages.
        method: The HTTP method.
        path: The path, relative to the client's base URL.
        **kwargs: Passed on to ``BaseHttpClient.request`` (``json=``, ...).

    Returns:
        The response text, as received.

    Raises:
        SourceAuthError: On a 401: the key is missing or wrong.
        SourceRefusedError: On a 403 or 429: quota, over-use or permission.
        SourceUnavailableError: On any other status, or when retries run out.
    """
    try:
        response = await client.request(method, path, **kwargs)
    except RetriesExhaustedError as exc:
        raise SourceUnavailableError(f"{source}: {exc}") from None
    except httpx.HTTPStatusError as exc:
        response = exc.response
    status = response.status_code
    if status in ANSWER_STATUS:
        return response.text
    said = f"{source}: HTTP {status}{_what_it_said(response)}"
    if status == 401:
        raise SourceAuthError(said)
    if status in {403, 429}:
        raise SourceRefusedError(said)
    raise SourceUnavailableError(said)


def _what_it_said(response: httpx.Response) -> str:
    """The error's message from a JSON body, if it has one."""
    try:
        body = response.json()
    except json.JSONDecodeError, UnicodeDecodeError:
        return ""
    if isinstance(body, dict) and body.get("message"):
        return f" {body['message']}"
    return ""
