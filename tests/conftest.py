"""Fixtures shared by the whole test suite.

pytest finds this file automatically; any test can use these fixtures by
naming them as parameters.
"""

from __future__ import annotations

import asyncio
import os

import pytest

ENV_PREFIX = "COFFEE_REVIEW_"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove project env vars so a developer's shell can't change test results."""
    for name in list(os.environ):
        if name.startswith(ENV_PREFIX):
            monkeypatch.delenv(name)


@pytest.fixture
def recorded_sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Replace asyncio.sleep with a stub that records each delay and returns at once.

    Retry tests use this to check backoff delays without actually waiting.
    """
    delays: list[float] = []

    async def fake_sleep(delay: float, *args: object, **kwargs: object) -> None:
        delays.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    return delays
