"""Fixtures shared by the whole test suite.

pytest finds this file automatically; any test can use these fixtures by
naming them as parameters.
"""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING

import pytest

from coffee_review.config import get_settings

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

ENV_PREFIX = "COFFEE_REVIEW_"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """Keep the developer's own settings out of every test.

    - Removes project env vars. pydantic-settings matches names ignoring case,
      so this does too.
    - Runs the test from an empty temp directory, so a real ``.env`` in the
      checkout is never found.
    - Clears the cached ``get_settings()`` before and after, so one test's
      settings never carry over into another.
    """
    for name in list(os.environ):
        if name.upper().startswith(ENV_PREFIX):
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


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
